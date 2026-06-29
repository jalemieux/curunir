"""Tests for the persona UI module registry (src/modules.py).

A module's UI surface (tab + read endpoints) is enabled iff its gating skill
is explicitly named in the active persona's skill allowlist. A falsy allowlist
(``None``/empty — the generic ``default`` persona) enables no modules.
"""
from src.modules import MODULES, enabled_modules


def test_none_allowlist_enables_no_modules():
    assert enabled_modules(None) == []


def test_empty_allowlist_enables_no_modules():
    assert enabled_modules([]) == []


def test_finance_style_allowlist_enables_portfolio():
    allow = ["balance-sheet", "research", "web-fetch"]
    mods = enabled_modules(allow)
    assert [m.panel_id for m in mods] == ["portfolio"]


def test_marketing_style_allowlist_excludes_portfolio():
    # marketing allowlists skills but not balance-sheet.
    allow = ["crm", "gtm-plan", "research"]
    assert enabled_modules(allow) == []


def test_registry_shape():
    portfolio = next(m for m in MODULES if m.name == "portfolio")
    assert portfolio.gating_skill == "balance-sheet"
    assert portfolio.panel_id == "portfolio"
    assert portfolio.panel_label == "Balance Sheet"
    assert "/api/portfolio" in portfolio.endpoint_prefixes
