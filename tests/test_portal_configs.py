"""Tests for run.parse_portal_configs — per-portal env var parsing.

Multi-portal config uses role-named env var pairs
(CURUNIR_PORTAL_PUBLIC_*, CURUNIR_PORTAL_INTERNAL_*); the legacy
CURUNIR_PORTAL_URL/TOKEN pair is retained as a single-portal fallback.
"""

import pytest

from run import parse_portal_configs


def test_no_portal_vars_returns_empty():
    assert parse_portal_configs({}) == []


def test_legacy_single_portal_fallback():
    env = {
        "CURUNIR_PORTAL_URL": "ws://legacy/ws/agent",
        "CURUNIR_PORTAL_TOKEN": "legacy-tok",
    }
    assert parse_portal_configs(env) == [
        ("portal", "ws://legacy/ws/agent", "legacy-tok"),
    ]


def test_named_public_and_internal_parsed():
    env = {
        "CURUNIR_PORTAL_PUBLIC_URL": "ws://public/ws/agent",
        "CURUNIR_PORTAL_PUBLIC_TOKEN": "pub-tok",
        "CURUNIR_PORTAL_INTERNAL_URL": "ws://internal/ws/agent",
        "CURUNIR_PORTAL_INTERNAL_TOKEN": "int-tok",
    }
    configs = parse_portal_configs(env)
    assert ("public", "ws://public/ws/agent", "pub-tok") in configs
    assert ("internal", "ws://internal/ws/agent", "int-tok") in configs
    assert len(configs) == 2


def test_only_internal_named_parsed():
    env = {
        "CURUNIR_PORTAL_INTERNAL_URL": "ws://internal/ws/agent",
        "CURUNIR_PORTAL_INTERNAL_TOKEN": "int-tok",
    }
    assert parse_portal_configs(env) == [
        ("internal", "ws://internal/ws/agent", "int-tok"),
    ]


def test_named_pair_missing_token_rejected():
    env = {"CURUNIR_PORTAL_PUBLIC_URL": "ws://public/ws/agent"}
    with pytest.raises(ValueError):
        parse_portal_configs(env)


def test_named_pair_missing_url_rejected():
    env = {"CURUNIR_PORTAL_INTERNAL_TOKEN": "int-tok"}
    with pytest.raises(ValueError):
        parse_portal_configs(env)


def test_legacy_pair_missing_token_rejected():
    env = {"CURUNIR_PORTAL_URL": "ws://legacy/ws/agent"}
    with pytest.raises(ValueError):
        parse_portal_configs(env)


def test_legacy_mixed_with_named_rejected():
    """Legacy is a single-portal fallback — mixing it with a named pair is
    an ambiguous config and is rejected."""
    env = {
        "CURUNIR_PORTAL_URL": "ws://legacy/ws/agent",
        "CURUNIR_PORTAL_TOKEN": "legacy-tok",
        "CURUNIR_PORTAL_PUBLIC_URL": "ws://public/ws/agent",
        "CURUNIR_PORTAL_PUBLIC_TOKEN": "pub-tok",
    }
    with pytest.raises(ValueError):
        parse_portal_configs(env)


def test_whitespace_only_values_treated_as_unset():
    env = {"CURUNIR_PORTAL_URL": "   ", "CURUNIR_PORTAL_TOKEN": "  "}
    assert parse_portal_configs(env) == []
