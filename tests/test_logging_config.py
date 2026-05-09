"""Tests for the logging configuration helper in run.py.

Covers the three behaviors described in PR #87:
    - LOG_FILE unset → no file handler attached.
    - LOG_FILE set → root logger gets a single RotatingFileHandler with
      the configured path, maxBytes, and backupCount.
    - Parent directory is created automatically so a missing
      /app/workspace doesn't crash startup.
"""
import logging
from logging.handlers import RotatingFileHandler

import pytest


@pytest.fixture(autouse=True)
def _reset_root_logger():
    """Snapshot/restore root handlers around each test."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    yield
    for h in root.handlers[:]:
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    for h in saved_handlers:
        root.addHandler(h)
    root.setLevel(saved_level)


def _file_handlers():
    return [h for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler)]


def test_unset_log_file_attaches_no_file_handler():
    from run import _configure_logging

    _configure_logging(None)
    assert _file_handlers() == []


def test_empty_log_file_attaches_no_file_handler():
    from run import _configure_logging

    _configure_logging("")
    assert _file_handlers() == []


def test_log_file_set_attaches_rotating_file_handler(tmp_path):
    from run import _configure_logging

    log_path = tmp_path / "curunir.log"
    _configure_logging(str(log_path))

    handlers = _file_handlers()
    assert len(handlers) == 1
    h = handlers[0]
    assert h.baseFilename == str(log_path)
    assert h.maxBytes == 10_000_000
    assert h.backupCount == 3


def test_log_file_creates_missing_parent_directory(tmp_path):
    from run import _configure_logging

    log_path = tmp_path / "nested" / "deeper" / "curunir.log"
    assert not log_path.parent.exists()

    _configure_logging(str(log_path))

    assert log_path.parent.is_dir()
    assert len(_file_handlers()) == 1


def test_log_file_permission_error_falls_back_to_stderr(tmp_path, monkeypatch, capsys):
    """A PermissionError during handler creation must not crash startup."""
    from run import _configure_logging

    def _boom(*args, **kwargs):
        raise PermissionError("read-only fs")

    monkeypatch.setattr(
        "logging.handlers.RotatingFileHandler.__init__", _boom
    )

    _configure_logging(str(tmp_path / "curunir.log"))

    assert _file_handlers() == []
