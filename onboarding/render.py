"""Render context.default/identity.md from a filled-in questions.md.

Usage: python onboarding/render.py

Reads onboarding/questions.md, parses the user's answers, fills the template
in onboarding/identity_template.md, and writes the result to
context.default/identity.md. The existing onboarding/bootstrap.py copies that
file to context/identity.md on first run.
"""

import re
import sys
from pathlib import Path

ONBOARDING_DIR = Path(__file__).parent
REPO_ROOT = ONBOARDING_DIR.parent
QUESTIONS_FILE = ONBOARDING_DIR / "questions.md"
TEMPLATE_FILE = ONBOARDING_DIR / "identity_template.md"
OUTPUT_FILE = REPO_ROOT / "context.default" / "identity.md"

PERSONAS = {
    "pragmaticpeer": (
        "Be direct, dry, and opinionated. Treat the user as a peer — push back "
        "when you disagree. Skip preamble and filler. Lead with the answer."
    ),
    "executiveassistant": (
        "Be warm but efficient. Light on small talk. Anticipate next steps. "
        "Don't editorialize. Acknowledge briefly, then act."
    ),
    "stoicbutler": (
        "Be formal, polished, and minimal. Anticipatory but distant — Jeeves "
        "energy. Confirm action quietly and move on."
    ),
    "friendlyconcierge": (
        "Be warm, conversational, and a little chatty. Check in. Make the user "
        "feel handled. Light context is welcome."
    ),
    "wittycompanion": (
        "Use dry humor and light banter. Smart-friend energy — never cringe, "
        "never forced. Be competent first, witty second."
    ),
    "chiefofstaff": (
        "Be proactive and strategic. Raise things the user didn't ask about. "
        "Surface tradeoffs and downstream implications."
    ),
}

STYLE_KEYWORDS = {
    "terse": (
        "Minimum words. No preamble, no recap, no 'happy to help.' Lead with "
        "the answer."
    ),
    "direct": (
        "Minimum words. No preamble, no recap, no 'happy to help.' Lead with "
        "the answer."
    ),
    "conversational": (
        "Natural back-and-forth. Light context is fine. Don't over-explain "
        "unless asked."
    ),
    "detailed": (
        "Walk through your reasoning. Show your work. Surface tradeoffs."
    ),
    "explanatory": (
        "Walk through your reasoning. Show your work. Surface tradeoffs."
    ),
}

DEFAULT_BOUNDARIES = (
    "Always confirm before sending messages, spending money, or making "
    "irreversible changes."
)

NAME_HINT = (
    "\n\n*Note: you don't yet know the user's name. Ask for it on first "
    "interaction and offer to update `context/identity.md`.*"
)


def _norm(s: str) -> str:
    """Lowercase, strip whitespace and hyphens — for fuzzy key matching."""
    return re.sub(r"[\s\-_]", "", s.lower())


def parse_answers(text: str) -> dict[int, str]:
    """Extract answers from a filled-in questions.md.

    Splits on `### N.` headers and captures text after each `_Answer:_` marker
    up to the next `---` separator or end of file. Empty answers are omitted.
    """
    answers: dict[int, str] = {}
    chunks = re.split(r"^### (\d+)\.", text, flags=re.MULTILINE)
    # chunks = [pre, "1", body1, "2", body2, ...]
    for i in range(1, len(chunks), 2):
        qnum = int(chunks[i])
        body = chunks[i + 1]
        m = re.search(
            r"_Answer:_\s*(.*?)(?=^---|\Z)",
            body,
            flags=re.DOTALL | re.MULTILINE,
        )
        if m:
            answer = m.group(1).strip()
            if answer:
                answers[qnum] = answer
    return answers


def render_personality(answer: str | None) -> str:
    if not answer:
        answer = "Pragmatic peer"
    return PERSONAS.get(_norm(answer), answer)


def render_style(answer: str | None) -> str:
    if not answer:
        answer = "Conversational"
    key = _norm(answer)
    for keyword, expansion in STYLE_KEYWORDS.items():
        if keyword in key:
            return expansion
    return answer


def _bulletize(answer: str) -> str:
    """Format a list-like answer as markdown bullets.

    Handles newline-separated, dash-prefixed, or comma-separated input.
    """
    lines = [line.strip(" -*\t") for line in answer.split("\n") if line.strip()]
    if len(lines) == 1 and "," in lines[0]:
        lines = [p.strip() for p in lines[0].split(",") if p.strip()]
    return "\n".join(f"- {line}" for line in lines)


def render_use_cases_section(name: str, answer: str | None) -> str:
    if not answer:
        return ""
    bullets = _bulletize(answer)
    return (
        f"\n## What {name} wants help with\n\n"
        f"{bullets}\n\n"
        "These are the recurring jobs. Recognize when a request fits one of "
        "them and load the relevant skill proactively.\n"
    )


def render_boundaries(answer: str | None) -> str:
    if not answer:
        return f"- {DEFAULT_BOUNDARIES}"
    return _bulletize(answer)


def render_about(name: str, role: str | None, timezone: str, extra: str | None) -> str:
    parts: list[str] = []
    if role:
        parts.append(role)
    sentence_name = name[0].upper() + name[1:]
    parts.append(
        f'{sentence_name} is in the {timezone} timezone. Anchor all "today / '
        'tomorrow / this week" references to that zone.'
    )
    if extra:
        parts.append(extra)
    return "\n\n".join(parts)


def render_identity(answers: dict[int, str], template: str) -> str:
    name = answers.get(1, "the user")
    name_hint = NAME_HINT if 1 not in answers else ""

    rendered = template
    rendered = rendered.replace("{{name}}", name)
    rendered = rendered.replace("{{name_hint}}", name_hint)
    rendered = rendered.replace(
        "{{personality}}", render_personality(answers.get(7))
    )
    rendered = rendered.replace(
        "{{about}}",
        render_about(name, answers.get(2), answers.get(4, "UTC"), answers.get(8)),
    )
    rendered = rendered.replace(
        "{{use_cases_section}}",
        render_use_cases_section(name, answers.get(3)),
    )
    rendered = rendered.replace(
        "{{communication}}", render_style(answers.get(5))
    )
    rendered = rendered.replace(
        "{{boundaries}}", render_boundaries(answers.get(6))
    )
    return rendered


def main() -> int:
    if not QUESTIONS_FILE.exists():
        print(f"error: {QUESTIONS_FILE} not found", file=sys.stderr)
        return 1
    if not TEMPLATE_FILE.exists():
        print(f"error: {TEMPLATE_FILE} not found", file=sys.stderr)
        return 1

    answers = parse_answers(QUESTIONS_FILE.read_text())
    if not answers:
        print(
            f"warning: no answers found in {QUESTIONS_FILE.name} — using defaults. "
            "Edit it and re-run for a personalized identity.",
            file=sys.stderr,
        )

    rendered = render_identity(answers, TEMPLATE_FILE.read_text())
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(rendered)
    print(f"wrote {OUTPUT_FILE.relative_to(REPO_ROOT)} ({len(answers)}/8 answers used)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
