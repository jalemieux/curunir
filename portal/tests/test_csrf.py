import pytest

from portal import csrf


def test_issue_then_verify():
    token = csrf.issue_csrf(7)
    assert csrf.verify_csrf(7, token) is True


def test_verify_wrong_user_fails():
    token = csrf.issue_csrf(7)
    assert csrf.verify_csrf(8, token) is False


def test_verify_garbage_fails():
    assert csrf.verify_csrf(7, "") is False
    assert csrf.verify_csrf(7, "deadbeef") is False
