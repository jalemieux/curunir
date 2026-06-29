#!/usr/bin/env python3
"""email-send CLI — thin adapter over src.channels.fastmail.

Sends a NEW outbound (cold) email via Fastmail SMTP. Credentials and the
recipient allowlist come from the environment (FASTMAIL_USER, FASTMAIL_PASSWORD,
FASTMAIL_INBOX, EMAIL_RESTRICT_OUTBOUND, EMAIL_ALLOWED_SENDERS); the client
enforces the allowlist. Do NOT use this to reply inside an email-channel thread —
the channel sends your final assistant message as the reply automatically.

Prints {"sent": true, "id": ...} to stdout on success. On failure prints
{"error": ..., "hint": ...} and exits 1; argparse usage errors exit 2.

The agent invokes this via the bash tool, from the repo root:

    python skills/email-send/email_send.py send \
        --to you@example.com --subject "Subject" --body-file /tmp/body.txt

Tests import it by path and call cmd_send() with a fake client factory, so no
test hits the network.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from src.channels._render import render_html  # noqa: E402
from src.channels.fastmail import FastmailError, build_client_from_env  # noqa: E402


def _split_addrs(values: list[str] | None) -> list[str]:
    """Flatten repeated and comma-separated address flags into one list."""
    out: list[str] = []
    for v in values or []:
        out.extend(a.strip() for a in v.split(",") if a.strip())
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="email_send.py", description="Send a new outbound email via Fastmail SMTP."
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("send", help="Send a new outbound email.")
    s.add_argument(
        "--to", action="append", required=True, metavar="ADDR",
        help="Recipient address. Repeatable; comma-separated values also accepted.",
    )
    s.add_argument("--cc", action="append", metavar="ADDR", help="CC address (repeatable).")
    s.add_argument("--bcc", action="append", metavar="ADDR", help="BCC address (repeatable).")
    s.add_argument("--subject", required=True)
    body = s.add_mutually_exclusive_group(required=True)
    body.add_argument("--body", help="Plain-text body, inline.")
    body.add_argument(
        "--body-file", dest="body_file",
        help="Path to a file holding the plain-text body (preferred for long content).",
    )
    s.add_argument(
        "--html-file", dest="html_file",
        help="Optional path to an HTML body; the recipient's client picks text or HTML.",
    )
    s.add_argument(
        "--attach", action="append", metavar="PATH", help="Attachment file path (repeatable)."
    )
    return p


def _read_file(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _fail(msg: str, hint: str = "") -> int:
    out = {"error": msg}
    if hint:
        out["hint"] = hint
    print(json.dumps(out))
    return 1


def cmd_send(args, *, client_factory=build_client_from_env) -> int:
    to = _split_addrs(args.to)
    cc = _split_addrs(args.cc)
    bcc = _split_addrs(args.bcc)

    try:
        text_body = _read_file(args.body_file) if args.body_file else args.body
        # An explicit --html-file wins; otherwise auto-render the markdown body to
        # styled HTML at the transport boundary so proactive sends ship pretty HTML
        # with zero skill work. `or None` mirrors the reply path: an empty render
        # (e.g. markdown lib missing) degrades to a text-only send.
        if args.html_file:
            html_body = _read_file(args.html_file)
        else:
            html_body = render_html(text_body) or None
    except OSError as e:
        return _fail(f"could not read body file: {e}")

    async def _run() -> dict:
        client = client_factory()
        try:
            return await client.send_email(
                to=to,
                cc=cc or None,
                bcc=bcc or None,
                subject=args.subject,
                text_body=text_body,
                html_body=html_body,
                attachment_paths=args.attach or None,
            )
        finally:
            await client.aclose()

    try:
        result = asyncio.run(_run())
    except FastmailError as e:
        return _fail(str(e), "check FASTMAIL_* env vars and the EMAIL_ALLOWED_SENDERS allowlist")
    except Exception as e:  # noqa: BLE001 — surface any failure as a model-visible JSON error
        return _fail(f"send failed: {e}")

    msg_id = result.get("id") if isinstance(result, dict) else None
    print(json.dumps({"sent": True, "id": msg_id}))
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "send":
        return cmd_send(args)
    return 2  # unreachable: subparsers are required


if __name__ == "__main__":
    sys.exit(main())
