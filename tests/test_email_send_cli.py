"""Tests for the email-send skill CLI (skills/email-send/email_send.py).

The CLI is a thin adapter over src.channels.deadsimple.DeadsimpleClient. We load
it by path (it lives under skills/, not an importable package) and drive cmd_send
with a fake client factory so nothing hits the network.
"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_cli():
    spec = importlib.util.spec_from_file_location(
        "email_send_cli", ROOT / "skills" / "email-send" / "email_send.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cli = _load_cli()


class FakeClient:
    """Stand-in for DeadsimpleClient — records the send and the close."""

    def __init__(self):
        self.sent = None
        self.closed = False

    async def send_email(self, **kwargs):
        self.sent = kwargs
        return {"id": "msg_123"}

    async def aclose(self):
        self.closed = True


def _parse(*argv):
    return cli.build_parser().parse_args(list(argv))


def test_parser_send_basic():
    args = _parse("send", "--to", "a@x.com", "--subject", "Hi", "--body", "yo")
    assert args.cmd == "send"
    assert args.to == ["a@x.com"]
    assert args.subject == "Hi"
    assert args.body == "yo"


def test_send_calls_client_with_flattened_recipients():
    fake = FakeClient()
    args = _parse(
        "send", "--to", "a@x.com,b@x.com", "--to", "c@x.com",
        "--subject", "Hi", "--body", "hello",
    )
    rc = cli.cmd_send(args, client_factory=lambda: fake)
    assert rc == 0
    assert fake.sent["to"] == ["a@x.com", "b@x.com", "c@x.com"]
    assert fake.sent["subject"] == "Hi"
    assert fake.sent["text_body"] == "hello"
    assert fake.closed is True


def test_send_body_file(tmp_path):
    f = tmp_path / "body.txt"
    f.write_text("from file")
    fake = FakeClient()
    args = _parse("send", "--to", "a@x.com", "--subject", "S", "--body-file", str(f))
    rc = cli.cmd_send(args, client_factory=lambda: fake)
    assert rc == 0 and fake.sent["text_body"] == "from file"


def test_send_html_file(tmp_path):
    f = tmp_path / "b.html"
    f.write_text("<h1>hi</h1>")
    fake = FakeClient()
    args = _parse(
        "send", "--to", "a@x.com", "--subject", "S", "--body", "t", "--html-file", str(f)
    )
    rc = cli.cmd_send(args, client_factory=lambda: fake)
    assert rc == 0 and fake.sent["html_body"] == "<h1>hi</h1>"


def test_send_reports_message_id(capsys):
    fake = FakeClient()
    args = _parse("send", "--to", "a@x.com", "--subject", "S", "--body", "t")
    rc = cli.cmd_send(args, client_factory=lambda: fake)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out == {"sent": True, "id": "msg_123"}


def test_send_deadsimple_error_returns_1(capsys):
    def boom():
        raise cli.DeadsimpleError("recipient not allowed")

    args = _parse("send", "--to", "x@x.com", "--subject", "S", "--body", "t")
    rc = cli.cmd_send(args, client_factory=boom)
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and "error" in out


def test_missing_body_is_usage_error():
    with pytest.raises(SystemExit) as ei:
        _parse("send", "--to", "a@x.com", "--subject", "S")
    assert ei.value.code == 2


def test_body_and_body_file_are_mutually_exclusive(tmp_path):
    f = tmp_path / "b.txt"
    f.write_text("x")
    with pytest.raises(SystemExit) as ei:
        _parse("send", "--to", "a@x.com", "--subject", "S", "--body", "t", "--body-file", str(f))
    assert ei.value.code == 2
