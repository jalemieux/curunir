"""Pluggable brokerage adapter framework.

A deployment enables zero, one, or several brokerage integrations via the
``BROKERAGE_ADAPTERS`` env var alone (config-only enablement). Each adapter
implements the :class:`~src.portfolio.brokers.base.BrokerageAdapter` protocol,
returning normalized :class:`BrokerAccount` / :class:`BrokerPosition`
dataclasses that the reconcile layer (``src.portfolio.reconcile``) diffs
against the local balance sheet — never a silent overwrite.

E*TRADE is the first adapter (:mod:`src.portfolio.brokers.etrade`), signing
requests with the stdlib OAuth 1.0a signer (:mod:`_oauth1`).
"""
from __future__ import annotations

from src.portfolio.brokers.base import (
    BrokerAccount,
    BrokerageAdapter,
    BrokerAuthRequired,
    BrokerError,
    BrokerPosition,
)

__all__ = [
    "BrokerAccount",
    "BrokerPosition",
    "BrokerageAdapter",
    "BrokerError",
    "BrokerAuthRequired",
]
