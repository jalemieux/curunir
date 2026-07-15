"""Adapter contract for brokerage integrations.

The framework speaks in two normalized dataclasses — :class:`BrokerAccount`
and :class:`BrokerPosition` — so the reconcile layer and the UI never touch a
vendor-specific payload shape. Any adapter implements the three-step auth API
(``auth_start`` → ``auth_complete`` → ``auth_status``) plus ``fetch_accounts``
/ ``fetch_positions``; a registry (``registry.enabled_adapters``) builds the
enabled set from config.

Auth failures raise :class:`BrokerAuthRequired` (a re-auth is needed in the web
console); everything else adapter-specific raises :class:`BrokerError`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


class BrokerError(Exception):
    """Any adapter-level failure (network, parse, unexpected API shape)."""


class BrokerAuthRequired(BrokerError):
    """The adapter has no valid token and needs an interactive re-auth.

    Raised when a token is missing or hard-expired. The operator completes the
    OAuth verifier flow in the Local Web UI's Balance Sheet tab — there is no
    CLI auth path.
    """


@dataclass
class BrokerAccount:
    """A normalized brokerage account.

    ``account_id`` is the stable, human-facing id used to key reconciliation
    against a local asset's ``account`` column. ``raw`` retains the vendor
    payload (e.g. E*TRADE's ``accountIdKey``) for follow-up position calls.
    """

    account_id: str
    name: str | None = None
    type: str | None = None
    institution: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class BrokerPosition:
    """A normalized position (aggregate qty for one ticker in one account).

    Broker positions are aggregate; the local model is specific-lot, so the
    reconcile layer compares broker ``qty`` against the *sum* of local lots.
    """

    account_id: str
    ticker: str
    qty: float
    price: float | None = None
    market_value: float | None = None
    cost_basis: float | None = None
    as_of: str | None = None
    description: str | None = None
    raw: dict = field(default_factory=dict)


@runtime_checkable
class BrokerageAdapter(Protocol):
    """The contract every brokerage integration implements.

    ``name`` is the registry key (matches the ``BROKERAGE_ADAPTERS`` token).
    The auth trio drives an interactive OAuth flow from any surface; the fetch
    methods return normalized dataclasses.
    """

    name: str

    def auth_status(self) -> dict:
        """Return ``{"authed": bool, ...}`` describing token state."""
        ...

    def auth_start(self) -> dict:
        """Begin auth. Return ``{"authorize_url": str, ...}``."""
        ...

    def auth_complete(self, verifier: str) -> dict:
        """Exchange the pasted verifier for access tokens and persist them."""
        ...

    def fetch_accounts(self) -> list[BrokerAccount]:
        """List the authenticated user's accounts."""
        ...

    def fetch_positions(self, account: BrokerAccount) -> list[BrokerPosition]:
        """List positions for one account."""
        ...
