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
    assert "portfolio" not in [m.panel_id for m in enabled_modules(allow)]


def test_marketing_style_allowlist_enables_crm():
    allow = ["crm", "gtm-plan", "research"]
    assert [m.panel_id for m in enabled_modules(allow)] == ["crm"]


def test_finance_style_allowlist_excludes_crm():
    allow = ["balance-sheet", "research", "web-fetch"]
    assert "crm" not in [m.panel_id for m in enabled_modules(allow)]


def test_registry_shape():
    portfolio = next(m for m in MODULES if m.name == "portfolio")
    assert portfolio.gating_skill == "balance-sheet"
    assert portfolio.panel_id == "portfolio"
    assert portfolio.panel_label == "Balance Sheet"
    assert "/api/portfolio" in portfolio.endpoint_prefixes


def test_crm_registry_shape():
    crm = next(m for m in MODULES if m.name == "crm")
    assert crm.gating_skill == "crm"
    assert crm.panel_id == "crm"
    assert crm.panel_label == "CRM"
    assert "/api/crm" in crm.endpoint_prefixes
