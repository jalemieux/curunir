"""Per-session repetition detector for tool calls.

Catches the failure mode where the agent emits N near-identical tool calls
(same Brave query with reordered terms, same URL with different fragments,
same bash command rephrased) — burning iterations without new information.

The detector is pure (no agent dependency, no async) so it can be unit-tested
independently of the loop. The agent owns one detector per session and calls
``observe`` immediately before dispatching each tool call.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, deque
from dataclasses import dataclass, field
from enum import Enum

# Defaults — tunable from AgentConfig.
EXACT_NUDGE_THRESHOLD = 3       # 3rd identical signature triggers a nudge
EXACT_BLOCK_THRESHOLD = 10      # 10th identical signature blocks dispatch
SIMILAR_WINDOW = 5              # how many recent signatures to compare against
SIMILAR_JACCARD = 0.5           # Jaccard similarity for "near-duplicate" — real-world
                                # repeat-search queries cluster around half-token-overlap

_MAX_VALUE_CHARS = 500          # truncate long arg values before tokenizing
_TOKEN_PATTERN = re.compile(r"[\w]+", flags=re.UNICODE)


class Verdict(Enum):
    """Outcome of observing a tool call."""

    OK = "ok"
    NUDGE = "nudge"
    BLOCK = "block"


Signature = tuple[str, frozenset[str]]


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c)
    )


def _tokenize(value: str) -> set[str]:
    """Lowercase, strip accents/punctuation, return a deduped token set."""
    if len(value) > _MAX_VALUE_CHARS:
        value = value[:_MAX_VALUE_CHARS]
    folded = _strip_accents(value).lower()
    return set(_TOKEN_PATTERN.findall(folded))


def normalize_signature(
    name: str, args: dict, key_args: list[str]
) -> Signature:
    """Build a normalized signature for one tool call.

    Returns ``(tool_name, frozenset_of_tokens)`` where the token set comes from
    the values of the tool's "key" arguments (e.g. ``url`` for web_fetch,
    ``command`` for bash). Casing, accents, punctuation, and term order are all
    normalized away so that ``"X Y"`` and ``"y, x"`` collapse to the same set.
    """
    tokens: set[str] = set()
    if key_args:
        for k in key_args:
            if k in args and args[k] is not None:
                tokens.update(_tokenize(str(args[k])))
    elif args:
        # Unknown tool — fall back to the first argument's value.
        first_val = next(iter(args.values()))
        if first_val is not None:
            tokens.update(_tokenize(str(first_val)))
    return (name.lower(), frozenset(tokens))


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


@dataclass
class RepetitionDetector:
    """Per-session counter for near-identical tool calls.

    Tracks both exact-signature counts and a sliding window of recent
    signatures so we can flag near-duplicates that wouldn't hash-collide.
    """

    key_args: dict[str, list[str]]
    exact_nudge_threshold: int = EXACT_NUDGE_THRESHOLD
    exact_block_threshold: int = EXACT_BLOCK_THRESHOLD
    similar_window: int = SIMILAR_WINDOW
    similar_jaccard: float = SIMILAR_JACCARD

    _exact_counts: Counter[Signature] = field(default_factory=Counter)
    _recent: deque[Signature] = field(default_factory=deque)

    def __post_init__(self) -> None:
        if self._recent.maxlen != self.similar_window:
            self._recent = deque(maxlen=self.similar_window)

    def observe(self, name: str, args: dict) -> Verdict:
        """Record a tool call and return a verdict for the agent loop."""
        sig = normalize_signature(name, args, self.key_args.get(name, []))

        # Exact-match counter (strict signature equality).
        self._exact_counts[sig] += 1
        count = self._exact_counts[sig]

        verdict = Verdict.OK
        if count >= self.exact_block_threshold:
            verdict = Verdict.BLOCK
        elif count >= self.exact_nudge_threshold:
            verdict = Verdict.NUDGE

        # Near-duplicate scan: when the window holds (similar_window - 1) prior
        # calls and every one of them is the same tool with Jaccard at or above
        # threshold, the current call is the K-th consecutive near-duplicate
        # and we nudge — even if no exact signature has hit its own threshold.
        if (
            verdict == Verdict.OK
            and self.similar_window > 1
            and len(self._recent) >= self.similar_window - 1
            and all(
                prev[0] == sig[0]
                and _jaccard(prev[1], sig[1]) >= self.similar_jaccard
                for prev in self._recent
            )
        ):
            verdict = Verdict.NUDGE

        self._recent.append(sig)
        return verdict

    def clear(self) -> None:
        """Reset all counters — call when the session resets."""
        self._exact_counts.clear()
        self._recent.clear()
