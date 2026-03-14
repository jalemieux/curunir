"""Thin wrapper around the gog CLI for Gmail operations."""

import json
import subprocess


class GogError(Exception):
    """Raised when a gog command fails."""


def check_installed() -> None:
    """Verify gog CLI is available. Raises GogError if not."""
    try:
        subprocess.run(["gog", "--version"], capture_output=True, check=False)
    except FileNotFoundError:
        raise GogError("gog CLI is not installed. Install it from https://github.com/jantari/gog")


def _run(args: list[str]) -> subprocess.CompletedProcess:
    """Run a gog command, raising GogError on non-zero exit."""
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise GogError(result.stderr or f"gog exited with code {result.returncode}")
    return result


def _run_json(args: list[str]) -> list | dict:
    """Run a gog command and parse JSON output."""
    result = _run(args)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise GogError(f"Failed to parse gog JSON output: {e}")


def labels_list(account: str) -> list[dict]:
    """List all Gmail labels."""
    return _run_json(["gog", "gmail", "labels", "list", "--json", "--account", account])


def labels_create(label: str, account: str) -> None:
    """Create a Gmail label."""
    _run(["gog", "gmail", "labels", "create", label, "--account", account])


def search(query: str, account: str, max_results: int = 20) -> list[dict]:
    """Search Gmail threads matching a query."""
    return _run_json([
        "gog", "gmail", "search", query,
        "--json", "--max", str(max_results), "--account", account,
    ])


def thread_get(thread_id: str, account: str) -> dict:
    """Get a thread by ID."""
    return _run_json([
        "gog", "gmail", "thread", "get", thread_id,
        "--json", "--account", account,
    ])


def thread_download_attachments(thread_id: str, out_dir: str, account: str) -> None:
    """Download attachments from a thread to a directory."""
    _run([
        "gog", "gmail", "thread", "get", thread_id,
        "--download", "--out-dir", out_dir, "--account", account,
    ])


def send_reply(to: str, subject: str, body: str, reply_to_message_id: str, account: str) -> None:
    """Send a reply to a message."""
    _run([
        "gog", "gmail", "send",
        "--reply-to-message-id", reply_to_message_id,
        "--to", to,
        "--subject", subject,
        "--body", body,
        "--account", account,
    ])


def thread_modify(thread_id: str, add_label: str, account: str) -> None:
    """Modify a thread (add a label)."""
    _run([
        "gog", "gmail", "thread", "modify", thread_id,
        "--add", add_label, "--account", account,
    ])
