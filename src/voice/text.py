# src/voice/text.py
"""Text handling for voice mode (#551, docs/voice-mode-plan.md §3): cut streamed text
deltas into speakable sentences, and strip markdown so a voice can read them."""
from __future__ import annotations

import re

_SENTENCE_END = re.compile(r"[.!?][\"')\]]*(\s+)|(\n+)")  # group 1 or 2 is the whitespace to drop
_CLAUSE_BREAK = re.compile(r"[,;:](\s+)|\s[—–-](\s)")  # the punctuation stays with the head
_ABBREVIATIONS = {"dr", "mr", "mrs", "ms", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e", "no", "inc", "ltd"}


class SentenceChunker:
    """Feed text deltas, get back sentences as soon as they are speakable.

    First-chunk rule: the first sentence is flushed at its end, or at a clause break
    (comma, semicolon, colon, dash) once ``first_chunk_words`` words have accumulated,
    so the voice starts early on a long opening sentence. After that only sentence
    ends flush, which keeps the prosody natural. Fragments shorter than
    ``min_chars`` ("Dr.", "e.g.") and known abbreviations stay attached.
    """

    def __init__(self, first_chunk_words: int = 8, min_chars: int = 8):
        self.buf = ""
        self.first_chunk_words = first_chunk_words
        self.min_chars = min_chars
        self.started = False  # True once the first chunk has been flushed

    def feed(self, delta: str) -> list[str]:
        self.buf += delta
        out: list[str] = []
        while True:
            limit = self._open_fence()
            cut = self._sentence_cut(limit)
            if cut is None and not self.started:
                cut = self._clause_cut(limit)
            if cut is None:
                break
            head, self.buf = self.buf[:cut[0]].strip(), self.buf[cut[1]:]
            if head:
                out.append(head)
                self.started = True
        return out

    def _open_fence(self) -> int | None:
        """Start of an unclosed code fence, past which nothing is cut yet: the
        whole block must reach ``speakable`` in one piece."""
        if self.buf.count("```") % 2 == 0:
            return None
        return self.buf.rfind("```")

    def _inside_fence(self, pos: int) -> bool:
        """Is ``pos`` inside a closed code fence? Its newlines are not sentence ends."""
        start = 0
        while True:
            open_ = self.buf.find("```", start)
            if open_ < 0:
                return False
            close = self.buf.find("```", open_ + 3)
            if close < 0:
                return False
            if open_ <= pos < close + 3:
                return True
            start = close + 3

    def flush(self) -> str | None:
        rest = self.buf.strip()
        self.buf = ""
        return rest or None

    def _sentence_cut(self, limit: int | None = None) -> tuple[int, int] | None:
        for m in _SENTENCE_END.finditer(self.buf, 0, len(self.buf) if limit is None else limit):
            if self._inside_fence(m.start()):
                continue
            end = m.start(1) if m.group(1) is not None else m.start(2)
            head = self.buf[:end].strip()
            if len(head) < self.min_chars:
                continue
            last = head.rstrip(".!?\"')]").rsplit(None, 1)[-1].lower() if head else ""
            if last in _ABBREVIATIONS or (len(last) == 1 and last.isalpha()):
                continue  # "Dr. Smith", "J. Doe"
            return end, m.end()
        return None

    def _clause_cut(self, limit: int | None = None) -> tuple[int, int] | None:
        for m in _CLAUSE_BREAK.finditer(self.buf, 0, len(self.buf) if limit is None else limit):
            if self._inside_fence(m.start()):
                continue
            end = m.start(1) if m.group(1) is not None else m.start()
            if len(self.buf[:end].split()) >= self.first_chunk_words:
                return end, m.end()
        return None


_CODE_FENCE = re.compile(r"```.*?```", re.S)
_INLINE_CODE = re.compile(r"`([^`]*)`")
_TABLE_LINE = re.compile(r"^\s*\|.*\|\s*$", re.M)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.M)
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+", re.M)
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_BARE_URL = re.compile(r"https?://\S+")
_EMPHASIS = re.compile(r"(\*{1,3}|_{1,3}|~~)(?=\S)(.+?)(?<=\S)\1")
_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF⭐⬆↔-↪️]"
)
_BLANKS = re.compile(r"[ \t]+")
_LINES = re.compile(r"\n{3,}")


def speakable(text: str) -> str:
    """Markdown in, something a voice can read out.

    Code fences become one spoken sentence, tables are skipped, links keep their
    text, bare URLs become "a link", headings and bullets lose their markers,
    emphasis loses its asterisks, emoji are dropped. Regex only, no model.
    """
    text = _CODE_FENCE.sub(" I've put the code in the chat. ", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _TABLE_LINE.sub("", text)
    text = _HEADING.sub("", text)
    text = _BULLET.sub("", text)
    text = _LINK.sub(r"\1", text)
    text = _BARE_URL.sub("a link", text)
    text = _EMPHASIS.sub(r"\2", text)
    text = _EMOJI.sub("", text)
    text = _BLANKS.sub(" ", text)
    text = _LINES.sub("\n\n", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()
