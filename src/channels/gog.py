"""Thin wrapper around the gog CLI for Gmail operations."""

import base64
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
    data = _run_json(["gog", "gmail", "labels", "list", "--json", "--account", account])
    return data.get("labels", []) if isinstance(data, dict) else data


def labels_create(label: str, account: str) -> None:
    """Create a Gmail label."""
    _run(["gog", "gmail", "labels", "create", label, "--account", account])


def search(query: str, account: str, max_results: int = 20) -> list[dict]:
    """Search Gmail threads matching a query."""
    data = _run_json([
        "gog", "gmail", "search",
        "--json", "--max", str(max_results), "--account", account,
        "--", query,
    ])
    return data.get("threads", []) if isinstance(data, dict) else data


def thread_get(thread_id: str, account: str) -> dict:
    """Get a thread by ID, with messages normalized to flat format."""
    data = _run_json([
        "gog", "gmail", "thread", "get", thread_id,
        "--json", "--account", account,
    ])
    thread = data.get("thread", data) if isinstance(data, dict) else data
    thread["messages"] = [_normalize_message(m) for m in thread.get("messages", [])]
    return thread


def _get_header(headers: list[dict], name: str) -> str:
    """Extract a header value by name (case-insensitive)."""
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _decode_body(payload: dict) -> str:
    """Extract plain-text body from a Gmail message payload."""
    # Single-part message
    if not payload.get("parts"):
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        return ""

    # Multipart — prefer text/plain, fall back to text/html
    for preferred in ("text/plain", "text/html"):
        for part in payload["parts"]:
            if part.get("mimeType") == preferred:
                data = part.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    return ""


def _extract_attachments(payload: dict) -> list[dict]:
    """Extract attachment metadata from a Gmail message payload."""
    attachments = []
    for part in payload.get("parts", []):
        filename = part.get("filename", "")
        if filename:
            attachments.append({
                "filename": filename,
                "mimeType": part.get("mimeType", "application/octet-stream"),
                "size": part.get("body", {}).get("size", 0),
            })
    return attachments


def _normalize_message(raw: dict) -> dict:
    """Convert a raw Gmail API message into flat format expected by EmailChannel."""
    payload = raw.get("payload", {})
    headers = payload.get("headers", [])
    attachments = _extract_attachments(payload)
    return {
        "id": raw["id"],
        "thread_id": raw.get("threadId", ""),
        "from": _get_header(headers, "From"),
        "to": _get_header(headers, "To"),
        "subject": _get_header(headers, "Subject"),
        "body": _decode_body(payload),
        "attachments": attachments if attachments else [],
    }


def thread_download_attachments(thread_id: str, out_dir: str, account: str) -> None:
    """Download attachments from a thread to a directory."""
    _run([
        "gog", "gmail", "thread", "get", thread_id,
        "--download", "--out-dir", out_dir, "--account", account,
    ])


def send_reply(
    to: str, subject: str, body: str, reply_to_message_id: str, account: str,
    attachments: list[str] | None = None,
) -> None:
    """Send a reply to a message, optionally with file attachments."""
    cmd = [
        "gog", "gmail", "send",
        "--reply-to-message-id", reply_to_message_id,
        "--to", to,
        "--subject", subject,
        "--body", body,
        "--account", account,
    ]
    for path in (attachments or []):
        cmd.extend(["--attach", path])
    _run(cmd)


def thread_modify(thread_id: str, add_label: str, account: str) -> None:
    """Modify a thread (add a label)."""
    _run([
        "gog", "gmail", "thread", "modify", thread_id,
        "--add", add_label, "--account", account,
    ])
