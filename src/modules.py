"""Persona-gated UI module registry.

A *module* is a persona-specific vertical UI surface — a local-console tab plus
its read endpoints — that should only render on personas that own it. The
single source of truth tying *module → gating skill → panel id → endpoints*
lives here, so the channel (server-side endpoint guards, the ``meta`` frame)
and the frontend (tab filtering) can't drift.

Gating rule: a module is enabled **iff its gating skill is explicitly named in
the active persona's skill allowlist.** A falsy allowlist (``None``/empty — the
generic ``default`` persona) enables **no** modules. "None means all" applies
to *skills* (chat loadability), not to *modules* — specialized dashboards
require explicit naming, so the generic assistant gets none.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Module:
    name: str
    gating_skill: str
    panel_id: str
    panel_label: str
    endpoint_prefixes: tuple[str, ...]


MODULES: list[Module] = [
    Module(
        name="portfolio",
        gating_skill="balance-sheet",
        panel_id="portfolio",
        panel_label="Balance Sheet",
        endpoint_prefixes=("/api/portfolio",),
    ),
    Module(
        name="crm",
        gating_skill="crm",
        panel_id="crm",
        panel_label="CRM",
        endpoint_prefixes=("/api/crm",),
    ),
]


def enabled_modules(skill_allowlist: list[str] | None) -> list[Module]:
    """Modules whose gating skill is in ``skill_allowlist``.

    Returns ``[]`` for a falsy allowlist (``None``/empty) — modules require
    explicit naming, never the "None means all" skill default.
    """
    if not skill_allowlist:
        return []
    allowed = set(skill_allowlist)
    return [m for m in MODULES if m.gating_skill in allowed]
