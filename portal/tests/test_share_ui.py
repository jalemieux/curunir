"""Smoke checks for the per-conversation Share UI in portal/static/index.html.

These are pure string assertions against the static page — no fixtures, no
network, no DB. The point is to fail loudly if the share dropdown, the
markdown exporter, or the print stylesheet that hides internal frames is
ever accidentally removed or renamed.
"""

from pathlib import Path

import pytest

INDEX_HTML = Path(__file__).resolve().parents[1] / "static" / "index.html"


@pytest.fixture(scope="module")
def page() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def test_share_button_present(page: str):
    # The Share menu trigger lives in the header next to #new-btn.
    assert 'id="share-btn"' in page, "Share button is missing from the header"
    assert "Share" in page


def test_share_menu_items_present(page: str):
    # Both export paths must be reachable from the dropdown.
    assert "Download as Markdown" in page
    assert "Print" in page  # "Print" or "Print / Save as PDF" — partial match


def test_markdown_exporter_defined(page: str):
    # The export function must exist by name so the menu item can call it.
    assert "exportConversationAsMarkdown" in page


def test_print_css_hides_internal_frames(page: str):
    # The print stylesheet for conversation-share mode must hide every
    # element that represents internal/non-conversational content: tool
    # tickers (details.tools), system messages, response actions, the
    # composer, the sidebar, and the offline overlay. This is the
    # security/privacy boundary for the print export — if any of these
    # selectors disappear, internal frames could leak into a printed PDF.
    required_selectors = [
        "details.tools",       # tool calls / thinking ticker
        ".system-msg",         # system rows
        ".response-actions",   # per-response Copy/Print
        "#composer",
        "#sidebar",
        "footer",
    ]
    for sel in required_selectors:
        assert sel in page, f"print stylesheet missing selector: {sel}"

    # And there must be an @media print block scoping the share-print rules.
    assert "@media print" in page
    assert "printing-share" in page, (
        "expected a body.printing-share scope for the share-print stylesheet"
    )
