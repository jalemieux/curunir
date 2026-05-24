"""Tests for the scratch (ephemeral conversation) helper module."""
from src.agent.scratch import SCRATCH_SESSION_ID, is_scratch


def test_scratch_session_id_constant():
    assert SCRATCH_SESSION_ID == "scratch"


def test_is_scratch_true_for_scratch_session():
    assert is_scratch("scratch") is True


def test_is_scratch_false_for_other_sessions():
    assert is_scratch("portal") is False
    assert is_scratch("cli") is False
    assert is_scratch("some-uuid-1234") is False
    assert is_scratch("") is False


def test_is_scratch_none_safe():
    assert is_scratch(None) is False
