#!/usr/bin/env python3
"""gmail-draft CLI — create a DRAFT in the user's Gmail account (never sends).

Talks to the Gmail REST API with stdlib urllib (zero new deps). The only Gmail
endpoint this file calls is `users.drafts.create`; there is no send code path.
Note the OAuth scope itself (`gmail.compose`, the narrowest one that can create
a draft) would also permit sending, so "draft-only" is enforced here, not by
Google.

Credentials come from the environment: GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET,
GMAIL_REFRESH_TOKEN. Each run trades the refresh token for a short-lived access
token, so there is no token file to keep and no manual re-auth.

    # once, by the operator, at a shell on a machine with a browser:
    python skills/gmail-draft/gmail_draft.py auth

    # by the agent, via the bash tool, from the repo root:
    python skills/gmail-draft/gmail_draft.py draft \
        --to you@example.com --subject "Subject" --body-file /tmp/body.txt

Prints {"drafted": true, "id": ...} on success. On failure prints
{"error": ..., "hint": ...} and exits 1; argparse usage errors exit 2.

Tests import it by path and call cmd_draft() with a fake `post`, so no test
hits the network.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from src.channels._render import render_html  # noqa: E402

SCOPE = "https://www.googleapis.com/auth/gmail.compose"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DRAFTS_URL = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"

_AUTH_HINT = (
    "set GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET and GMAIL_REFRESH_TOKEN; the operator "
    "gets the refresh token by running `python skills/gmail-draft/gmail_draft.py auth`"
)


class GmailError(Exception):
    """A failed Google call. `status` is the HTTP status (0 = no response)."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def _post(url: str, *, form: dict | None = None, body: dict | None = None,
          token: str | None = None) -> dict:
    """POST form-encoded or JSON data; return the decoded JSON response."""
    headers = {}
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
    else:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise GmailError(_error_text(e.read().decode(errors="replace")), e.code) from e
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise GmailError(f"could not reach Google: {e}") from e


def _error_text(raw: str) -> str:
    """Pull the human-readable reason out of a Google error body."""
    try:
        err = json.loads(raw)
    except ValueError:
        return raw[:300]
    if isinstance(err.get("error"), dict):  # Gmail API shape
        return err["error"].get("message") or raw[:300]
    # OAuth token endpoint shape
    return ": ".join(x for x in (err.get("error"), err.get("error_description")) if x) or raw[:300]


def _split_addrs(values: list[str] | None) -> list[str]:
    """Flatten repeated and comma-separated address flags into one list."""
    out: list[str] = []
    for v in values or []:
        out.extend(a.strip() for a in v.split(",") if a.strip())
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gmail_draft.py", description="Create a draft in Gmail. Never sends."
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("draft", help="Create a draft in the Gmail Drafts folder.")
    d.add_argument(
        "--to", action="append", required=True, metavar="ADDR",
        help="Recipient address. Repeatable; comma-separated values also accepted.",
    )
    d.add_argument("--cc", action="append", metavar="ADDR", help="CC address (repeatable).")
    d.add_argument("--bcc", action="append", metavar="ADDR", help="BCC address (repeatable).")
    d.add_argument("--subject", required=True)
    body = d.add_mutually_exclusive_group(required=True)
    body.add_argument("--body", help="Body (markdown), inline.")
    body.add_argument(
        "--body-file", dest="body_file",
        help="Path to a file holding the body (preferred for long content).",
    )
    d.add_argument(
        "--html-file", dest="html_file",
        help="Optional path to an HTML body; overrides the auto-rendered HTML part.",
    )
    a = sub.add_parser("auth", help="Operator-only: one-time OAuth consent, prints a refresh token.")
    a.add_argument("--port", type=int, default=8769, help="Loopback port for the OAuth redirect.")
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


def build_raw(*, to, cc, bcc, subject, text_body, html_body) -> str:
    """Assemble the RFC 822 message Gmail stores as the draft, base64url-encoded.

    No From header: Gmail fills in the account's own address.
    """
    msg = EmailMessage()
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        msg["Bcc"] = ", ".join(bcc)
    msg["Subject"] = subject
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def cmd_draft(args, *, post=_post, env=None) -> int:
    env = os.environ if env is None else env
    creds = {k: env.get(k, "") for k in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN")}
    missing = [k for k, v in creds.items() if not v]
    if missing:
        return _fail(f"Gmail is not configured: missing {', '.join(missing)}", _AUTH_HINT)

    try:
        text_body = _read_file(args.body_file) if args.body_file else args.body
        # Same rule as email-send: an explicit --html-file wins, otherwise the
        # markdown body is rendered; an empty render degrades to text-only.
        if args.html_file:
            html_body = _read_file(args.html_file)
        else:
            html_body = render_html(text_body) or None
    except OSError as e:
        return _fail(f"could not read body file: {e}")

    try:
        raw = build_raw(
            to=_split_addrs(args.to), cc=_split_addrs(args.cc), bcc=_split_addrs(args.bcc),
            subject=args.subject, text_body=text_body, html_body=html_body,
        )
    except ValueError as e:  # e.g. a newline smuggled into a header
        return _fail(f"invalid header value: {e}")

    try:
        access = post(TOKEN_URL, form={
            "grant_type": "refresh_token",
            "client_id": creds["GMAIL_CLIENT_ID"],
            "client_secret": creds["GMAIL_CLIENT_SECRET"],
            "refresh_token": creds["GMAIL_REFRESH_TOKEN"],
        })["access_token"]
    except (GmailError, KeyError) as e:
        return _fail(
            f"Gmail authorization failed: {e}",
            "the refresh token or client credentials were rejected (revoked, expired or "
            "mistyped); the operator must re-run `python skills/gmail-draft/gmail_draft.py auth` "
            "and update GMAIL_REFRESH_TOKEN",
        )

    try:
        result = post(DRAFTS_URL, body={"message": {"raw": raw}}, token=access)
    except GmailError as e:
        hint = ""
        if e.status in (401, 403):
            hint = ("the token lacks the gmail.compose scope or the Gmail API is not enabled "
                    "for the Google Cloud project; see the skill's Setup section")
        return _fail(f"draft not created: {e}", hint)

    print(json.dumps({"drafted": True, "id": result.get("id")}))
    return 0


def cmd_auth(args, *, post=_post, env=None) -> int:
    """One-time consent: open the URL, catch the redirect on loopback, print the token."""
    env = os.environ if env is None else env
    client_id, client_secret = env.get("GMAIL_CLIENT_ID", ""), env.get("GMAIL_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return _fail("set GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET first",
                     "create a Desktop-app OAuth client in Google Cloud; see the skill's Setup section")

    redirect = f"http://127.0.0.1:{args.port}"
    state = secrets.token_urlsafe(16)
    got: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — http.server API
            got.update(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Done. You can close this tab and return to the terminal.")

        def log_message(self, *a):
            pass

    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent", "state": state,
    })
    print(f"Open this URL in a browser on this machine and approve:\n\n{url}\n", file=sys.stderr)
    with HTTPServer(("127.0.0.1", args.port), Handler) as srv:
        while "code" not in got and "error" not in got:  # ignore favicon etc.
            srv.handle_request()

    if got.get("state") != state or "code" not in got:
        return _fail(f"consent failed: {got.get('error', 'state mismatch')}")
    try:
        tok = post(TOKEN_URL, form={
            "grant_type": "authorization_code", "code": got["code"],
            "client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect,
        })
    except GmailError as e:
        return _fail(f"token exchange failed: {e}")
    if not tok.get("refresh_token"):
        return _fail("Google returned no refresh token",
                     "remove the app at myaccount.google.com/permissions and run auth again")
    print(f"GMAIL_REFRESH_TOKEN={tok['refresh_token']}")
    print("Add that line to .env and restart curunir.", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "draft":
        return cmd_draft(args)
    if args.cmd == "auth":
        return cmd_auth(args)
    return 2  # unreachable: subparsers are required


if __name__ == "__main__":
    sys.exit(main())
