"""Shared markdown→PDF helper — the single chokepoint for report attachments.

Rendering agent-authored markdown to PDF with pandoc's default engine (pdflatex)
crashes on common emoji the agent routinely emits — e.g. ✅ (U+2705) / ❌ (U+274C):

    ! LaTeX Error: Unicode character ✅ (U+2705) not set up for use with LaTeX.

This module removes that failure class deterministically and renders robustly:

1. **Sanitize** — strip emoji / pictographic codepoints that no LaTeX engine can
   typeset, while preserving broad typographic Unicode (curly quotes, em-dashes,
   accents, …). The source ``.md`` is never mutated; sanitization happens on an
   in-memory copy piped to pandoc.
2. **xelatex first** — render with ``--pdf-engine=xelatex`` and a DejaVu mainfont,
   which covers far more Unicode than pdflatex's default.
3. **pdflatex fallback** — if xelatex is unavailable or fails (e.g. the DejaVu
   font isn't installed), retry with pdflatex. Because emoji are already
   sanitized away, the pdflatex pass no longer hits the U+2705 failure class.

Callers attach the produced ``.pdf``; if every engine fails (``Md2PdfError``),
they fall back to attaching the ``.md`` as before.

Usage:
    python -m src.md2pdf INPUT.md [OUTPUT.pdf] [--no-sanitize]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Engines tried in order: xelatex (broad Unicode) then pdflatex (always present
# in the texlive-latex image, the graceful-degradation tier).
ENGINES = ("xelatex", "pdflatex")

# Fonts requested for the xelatex pass. DejaVu ships in the Docker image and
# covers far more Unicode than Latin Modern. If absent, xelatex fails and we
# fall back to pdflatex (which ignores these).
MAINFONT = "DejaVu Serif"
MONOFONT = "DejaVu Sans Mono"

# Unicode ranges to strip: emoji and pictographs that no LaTeX engine typesets.
# Deliberately scoped to pictographic blocks so typographic Unicode (General
# Punctuation U+2000–206F: em-dash, curly quotes, ellipsis, bullet; Latin
# accents; etc.) is preserved.
_EMOJI_RANGES = (
    (0x1F000, 0x1FAFF),  # Mahjong/Domino/Cards + all SMP emoji & pictograph blocks
    (0x1F1E6, 0x1F1FF),  # Regional indicator symbols (flags)
    (0x2600, 0x27BF),    # Misc symbols + Dingbats (incl. ✅ U+2705, ❌ U+274C, ✔ ✖)
    (0x2B00, 0x2BFF),    # Misc symbols & arrows (incl. ⭐ U+2B50)
    (0x2190, 0x21FF),    # Arrows (← ↔ ⇒ … not typeset by default pdflatex)
    (0xFE00, 0xFE0F),    # Variation selectors (the emoji/text presentation modifier)
    (0x200D, 0x200D),    # Zero-width joiner (binds multi-codepoint emoji)
    (0x2300, 0x23FF),    # Misc technical (⌚ ⏰ ⏳ …)
)

_EMOJI_RE = re.compile(
    "[" + "".join(f"\\U{lo:08X}-\\U{hi:08X}" for lo, hi in _EMOJI_RANGES) + "]"
)


class Md2PdfError(RuntimeError):
    """Raised when no available pandoc engine could produce a PDF."""


@dataclass
class PandocResult:
    ok: bool
    stderr: str


def sanitize_markdown(text: str) -> str:
    """Strip LaTeX-hostile emoji/pictographs, preserving typographic Unicode.

    Idempotent: removing already-stripped text is a no-op.
    """
    return _EMOJI_RE.sub("", text)


def _run_pandoc(text: str, dst: Path, engine: str) -> PandocResult:
    """Invoke pandoc once with the given engine. Isolated for testability."""
    cmd = [
        "pandoc",
        "-f",
        "markdown",
        "-o",
        str(dst),
        "--pdf-engine",
        engine,
    ]
    if engine == "xelatex":
        cmd += ["-V", f"mainfont={MAINFONT}", "-V", f"monofont={MONOFONT}"]
    try:
        proc = subprocess.run(
            cmd,
            input=text.encode("utf-8"),
            capture_output=True,
        )
    except FileNotFoundError as exc:
        # pandoc itself missing — surface as a failed attempt so callers can
        # fall back to the .md.
        return PandocResult(ok=False, stderr=str(exc))
    return PandocResult(
        ok=proc.returncode == 0 and dst.exists(),
        stderr=proc.stderr.decode("utf-8", "replace"),
    )


def convert(src: str | Path, dst: str | Path | None = None, *, sanitize: bool = True) -> Path:
    """Render a markdown file to PDF, trying xelatex then pdflatex.

    The source markdown is read (and optionally sanitized in memory) but never
    rewritten. Returns the path to the produced PDF, or raises ``Md2PdfError``
    if every engine fails.
    """
    src = Path(src)
    dst = Path(dst) if dst is not None else src.with_suffix(".pdf")

    text = src.read_text(encoding="utf-8")
    if sanitize:
        text = sanitize_markdown(text)

    errors: list[str] = []
    for engine in ENGINES:
        result = _run_pandoc(text, dst, engine)
        if result.ok:
            return dst
        errors.append(f"[{engine}] {result.stderr.strip()}")

    raise Md2PdfError(
        f"all PDF engines failed for {src} (is pandoc + a LaTeX engine installed?):\n"
        + "\n".join(errors)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.md2pdf",
        description="Render a markdown file to PDF (xelatex→pdflatex, emoji-sanitized).",
    )
    parser.add_argument("input", type=Path, help="Source markdown file.")
    parser.add_argument(
        "output",
        type=Path,
        nargs="?",
        default=None,
        help="Output PDF path (default: input with .pdf suffix).",
    )
    parser.add_argument(
        "--no-sanitize",
        action="store_true",
        help="Skip emoji sanitization (render the markdown verbatim).",
    )
    args = parser.parse_args(argv)

    if not args.input.exists():
        print(f"error: no such file: {args.input}", file=sys.stderr)
        return 1
    try:
        out = convert(args.input, args.output, sanitize=not args.no_sanitize)
    except Md2PdfError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
