"""Config-driven brokerage adapter registry.

``enabled_adapters(env)`` parses the comma-separated ``BROKERAGE_ADAPTERS`` env
var and instantiates each named adapter. An unknown name is an explicit
``ValueError`` (config typo, fail loud). A known adapter that's missing its
per-adapter keys is *warned and skipped* — mirroring the persona
key-warning pattern — so a half-configured deployment still boots.

Enablement is config-only: nothing elsewhere changes to add or drop an adapter.
"""
from __future__ import annotations

import logging
import os
from typing import Mapping

from src.portfolio.brokers.base import BrokerageAdapter

logger = logging.getLogger(__name__)

_DEFAULT_TOKEN_FILE = "context/.etrade-tokens.json"

#: Adapter names the registry knows how to build.
KNOWN_ADAPTERS = ("etrade",)


def _build_etrade(env: Mapping[str, str]):
    key = env.get("ETRADE_CONSUMER_KEY", "").strip()
    secret = env.get("ETRADE_CONSUMER_SECRET", "").strip()
    if not key or not secret:
        logger.warning(
            "brokerage adapter 'etrade' is enabled but ETRADE_CONSUMER_KEY / "
            "ETRADE_CONSUMER_SECRET are unset — skipping it. Provision keys at "
            "the E*TRADE developer portal and set them in the environment."
        )
        return None
    from src.portfolio.brokers.etrade import ETradeAdapter
    return ETradeAdapter(
        consumer_key=key,
        consumer_secret=secret,
        token_file=env.get("ETRADE_TOKEN_FILE", "").strip() or _DEFAULT_TOKEN_FILE,
        sandbox=env.get("ETRADE_SANDBOX", "").strip().lower() == "true",
    )


_BUILDERS = {"etrade": _build_etrade}


def enabled_adapters(env: Mapping[str, str] | None = None) -> list[BrokerageAdapter]:
    """Build the enabled brokerage adapters from ``BROKERAGE_ADAPTERS``.

    Empty/unset → ``[]``. An unknown name raises ``ValueError``. A configured-
    but-keyless adapter is warned and skipped (returns fewer than requested).
    """
    env = env if env is not None else os.environ
    raw = env.get("BROKERAGE_ADAPTERS", "") or ""
    names = [n.strip().lower() for n in raw.split(",") if n.strip()]
    adapters: list[BrokerageAdapter] = []
    for name in names:
        builder = _BUILDERS.get(name)
        if builder is None:
            raise ValueError(
                f"unknown brokerage adapter {name!r}; "
                f"known: {sorted(KNOWN_ADAPTERS)}"
            )
        adapter = builder(env)
        if adapter is not None:
            adapters.append(adapter)
    return adapters
