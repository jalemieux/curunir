"""Tests for the gmail-draft skill CLI (skills/gmail-draft/gmail_draft.py).

Loaded by path (it lives under skills/, not an importable package) and driven
with a fake `post`, so nothing hits the network.
"""
import base64
import email
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "gmail-draft" / "gmail_draft.py"


def _load_cli():
    spec = importlib.util.spec_from_file_location("gmail_draft_cli", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cli = _load_cli()

CREDS = {"client_id": "cid", "client_secret": "sec", "refresh_token": "rt"}


@pytest.fixture
def ENV(tmp_path):
    """Path of a populated credentials file."""
    path = tmp_path / ".gmail-draft.json"
    cli.save_creds(str(path), CREDS)
    return str(path)


class FakePost:
    """Records every call; answers the token refresh, then the draft create."""

    def __init__(self):
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url == cli.TOKEN_URL:
            return {"access_token": "at"}
        return {"id": "r-123", "message": {"id": "m1"}}

    def message(self):
        raw = self.calls[-1][1]["body"]["message"]["raw"]
        return email.message_from_bytes(base64.urlsafe_b64decode(raw))


def _parse(*argv):
    return cli.build_parser().parse_args(list(argv))


def test_draft_refreshes_token_then_creates_draft(capsys, ENV):
    post = FakePost()
    args = _parse(
        "draft", "--to", "a@x.com,b@x.com", "--cc", "c@x.com", "--bcc", "d@x.com",
        "--subject", "Hi", "--body", "Some **bold** text.",
    )
    rc = cli.cmd_draft(args, post=post, path=ENV)
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == {"drafted": True, "id": "r-123"}

    (token_url, token_kw), (draft_url, draft_kw) = post.calls
    assert token_url == cli.TOKEN_URL
    assert token_kw["form"] == {
        "grant_type": "refresh_token", "client_id": "cid",
        "client_secret": "sec", "refresh_token": "rt",
    }
    assert draft_url == cli.DRAFTS_URL and draft_kw["token"] == "at"

    msg = post.message()
    assert msg["To"] == "a@x.com, b@x.com"
    assert msg["Cc"] == "c@x.com" and msg["Bcc"] == "d@x.com"
    assert msg["Subject"] == "Hi"
    assert msg["From"] is None  # Gmail fills in the account address
    parts = {p.get_content_type(): p.get_payload(decode=True).decode() for p in msg.walk()
             if not p.is_multipart()}
    assert "Some **bold** text." in parts["text/plain"]
    assert "<strong>bold</strong>" in parts["text/html"]


def test_draft_body_file_and_html_file(tmp_path, ENV):
    body = tmp_path / "b.txt"
    body.write_text("from file")
    html = tmp_path / "b.html"
    html.write_text("<h1>custom</h1>")
    post = FakePost()
    args = _parse("draft", "--to", "a@x.com", "--subject", "S",
                  "--body-file", str(body), "--html-file", str(html))
    assert cli.cmd_draft(args, post=post, path=ENV) == 0
    parts = {p.get_content_type(): p.get_payload(decode=True).decode() for p in post.message().walk()
             if not p.is_multipart()}
    assert parts["text/plain"].strip() == "from file"
    assert parts["text/html"].strip() == "<h1>custom</h1>"


def test_empty_render_falls_back_to_text_only(monkeypatch, ENV):
    monkeypatch.setattr(cli, "render_html", lambda _text: "")
    post = FakePost()
    args = _parse("draft", "--to", "a@x.com", "--subject", "S", "--body", "hi")
    assert cli.cmd_draft(args, post=post, path=ENV) == 0
    assert post.message().get_content_type() == "text/plain"


def test_missing_credentials_file_makes_no_call(capsys, tmp_path):
    post = FakePost()
    args = _parse("draft", "--to", "a@x.com", "--subject", "S", "--body", "t")
    rc = cli.cmd_draft(args, post=post, path=str(tmp_path / "absent.json"))
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and post.calls == []
    assert out["error"] == "Gmail is not configured" and "auth" in out["hint"]


def test_credentials_are_never_read_from_the_environment(monkeypatch, capsys, tmp_path):
    # The bash tool passes the whole environment to every command, so the token
    # must not live there; a stray variable is ignored, not honoured.
    for k in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"):
        monkeypatch.setenv(k, "x")
    post = FakePost()
    args = _parse("draft", "--to", "a@x.com", "--subject", "S", "--body", "t")
    assert cli.cmd_draft(args, post=post, path=str(tmp_path / "absent.json")) == 1
    assert post.calls == []
    assert "environ.get(\"GMAIL" not in SCRIPT.read_text()


def test_credentials_file_is_owner_only_even_when_overwritten(tmp_path):
    path = tmp_path / ".gmail-draft.json"
    path.write_text("{}")
    path.chmod(0o644)
    cli.save_creds(str(path), CREDS)
    assert path.stat().st_mode & 0o777 == 0o600
    assert cli.load_creds(str(path)) == CREDS


def test_credentials_path_follows_the_shared_dir(monkeypatch):
    monkeypatch.setenv("CURUNIR_SHARED_DIR", "/data/ctx")
    assert cli.creds_path() == "/data/ctx/.gmail-draft.json"
    monkeypatch.delenv("CURUNIR_SHARED_DIR")
    assert cli.creds_path() == "context/.gmail-draft.json"


def test_auth_refuses_without_a_terminal(monkeypatch, capsys, tmp_path):
    # The agent's bash tool has no tty, so it cannot run the consent flow.
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False, raising=False)
    rc = cli.cmd_auth(_parse("auth"), path=str(tmp_path / "c.json"))
    assert rc == 1 and "operator-only" in json.loads(capsys.readouterr().out)["error"]


def test_rejected_refresh_token_is_a_clear_auth_error(capsys, ENV):
    def post(url, **kwargs):
        raise cli.GmailError("invalid_grant: Token has been expired or revoked.", 400)

    args = _parse("draft", "--to", "a@x.com", "--subject", "S", "--body", "t")
    rc = cli.cmd_draft(args, post=post, path=ENV)
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert "authorization failed" in out["error"] and "invalid_grant" in out["error"]
    assert "re-run" in out["hint"]


def test_draft_create_forbidden_hints_at_scope(capsys, ENV):
    def post(url, **kwargs):
        if url == cli.TOKEN_URL:
            return {"access_token": "at"}
        raise cli.GmailError("Request had insufficient authentication scopes.", 403)

    args = _parse("draft", "--to", "a@x.com", "--subject", "S", "--body", "t")
    rc = cli.cmd_draft(args, post=post, path=ENV)
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and "draft not created" in out["error"] and "gmail.compose" in out["hint"]


def test_newline_in_subject_is_rejected_not_injected(capsys, ENV):
    post = FakePost()
    args = _parse("draft", "--to", "a@x.com", "--subject", "S\nBcc: evil@x.com", "--body", "t")
    rc = cli.cmd_draft(args, post=post, path=ENV)
    assert rc == 1 and post.calls == []
    assert "invalid header" in json.loads(capsys.readouterr().out)["error"]


def test_error_text_reads_both_google_error_shapes():
    assert cli._error_text('{"error": {"code": 403, "message": "nope"}}') == "nope"
    assert cli._error_text('{"error": "invalid_grant", "error_description": "bad"}') == "invalid_grant: bad"
    assert cli._error_text("<html>oops") == "<html>oops"


def test_missing_body_is_usage_error():
    with pytest.raises(SystemExit) as ei:
        _parse("draft", "--to", "a@x.com", "--subject", "S")
    assert ei.value.code == 2


def test_no_send_code_path():
    # Draft-only is enforced here, not by the OAuth scope (gmail.compose can
    # send). The only Gmail endpoint is drafts.create; there is no send command.
    src = SCRIPT.read_text()
    assert "/send" not in src and "messages" not in src
    with pytest.raises(SystemExit):
        _parse("send", "--to", "a@x.com", "--subject", "S", "--body", "t")


def test_auth_requests_only_the_compose_scope():
    assert cli.SCOPE == "https://www.googleapis.com/auth/gmail.compose"
