"""Shared brokerage orchestration — the reusable node between surfaces.

The opt-in ``portfolio`` tool, the Local Web UI readers, and the console's
broker routes all drive brokerage sync through *these* functions, so the read
and write semantics can't drift across surfaces. Each function takes an
already-built adapter list (from ``registry.enabled_adapters``) and returns a
JSON-serializable dict that never raises on a per-adapter auth/network failure
— those surface as ``needs_reauth`` / ``error`` fields the UI and tool render
as friendly messages instead of a stack trace.

``status``  — per-adapter auth state (no network).
``accounts`` — each adapter's accounts (one network round-trip per adapter).
``diff``    — reconcile broker positions against the local store (read only).
``sync``    — apply the conservative reconcile subset (writes).
"""
from __future__ import annotations

from src.portfolio import reconcile
from src.portfolio.brokers.base import (
    BrokerageAdapter,
    BrokerAuthRequired,
    BrokerError,
    BrokerPosition,
)


def status(adapters: list[BrokerageAdapter]) -> dict:
    """Per-adapter auth state. Pure — no network, never raises."""
    return {
        "available": bool(adapters),
        "adapters": [_safe_status(a) for a in adapters],
    }


def _safe_status(adapter: BrokerageAdapter) -> dict:
    try:
        st = dict(adapter.auth_status())
    except Exception as e:  # noqa: BLE001 - status must never raise
        st = {"name": getattr(adapter, "name", "?"), "authed": False,
              "error": str(e)}
    st.setdefault("name", getattr(adapter, "name", "?"))
    return st


def collect_positions(adapter: BrokerageAdapter) -> list[BrokerPosition]:
    """Every position across every account for one adapter."""
    positions: list[BrokerPosition] = []
    for account in adapter.fetch_accounts():
        positions.extend(adapter.fetch_positions(account))
    return positions


def _per_adapter(adapters, fn) -> dict:
    """Run ``fn(adapter)`` for each adapter, folding auth/network failures into
    a reported entry rather than raising."""
    out = []
    for adapter in adapters:
        name = getattr(adapter, "name", "?")
        try:
            out.append({"name": name, **fn(adapter)})
        except BrokerAuthRequired as e:
            out.append({"name": name, "needs_reauth": True, "message": str(e)})
        except BrokerError as e:
            out.append({"name": name, "error": str(e)})
    return {"available": bool(adapters), "adapters": out}


def accounts(adapters: list[BrokerageAdapter]) -> dict:
    return _per_adapter(
        adapters,
        lambda a: {"accounts": [_account_dict(x) for x in a.fetch_accounts()]},
    )


def diff(path: str, adapters: list[BrokerageAdapter]) -> dict:
    return _per_adapter(
        adapters,
        lambda a: {"diff": reconcile.broker_diff(path, collect_positions(a))},
    )


def sync(path: str, adapters: list[BrokerageAdapter]) -> dict:
    return _per_adapter(
        adapters,
        lambda a: {"report": reconcile.broker_apply(
            path, collect_positions(a), source=a.name)},
    )


def find(adapters: list[BrokerageAdapter], name: str | None = None):
    """Pick one adapter by ``name`` (or the sole adapter when ``name`` is None).

    Returns ``None`` if no match / ambiguous — the caller maps that to a 400.
    Used by the auth routes, where a verifier flow targets one specific broker.
    """
    if name:
        for a in adapters:
            if getattr(a, "name", None) == name:
                return a
        return None
    return adapters[0] if len(adapters) == 1 else None


def _account_dict(account) -> dict:
    return {
        "account_id": account.account_id,
        "name": account.name,
        "type": account.type,
        "institution": account.institution,
    }
