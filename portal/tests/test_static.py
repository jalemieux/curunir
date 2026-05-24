from pathlib import Path

import pytest


_INDEX_HTML = Path(__file__).resolve().parents[1] / "static" / "index.html"


@pytest.mark.asyncio
async def test_healthz_returns_ok(client):
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_set_user_body_keeps_slash_cmd_class():
    # The bold-pill styling still needs the `.slash-cmd` hook.
    assert "slash-cmd" in _INDEX_HTML.read_text()


def test_set_user_body_does_not_wrap_entire_message_in_code():
    # Regression guard for #221: setUserBody must not assign `text.trim()`
    # (the whole message) to the slash-cmd <code> element. Only the leading
    # `/<token>` should land inside the pill.
    src = _INDEX_HTML.read_text()
    # Locate the function body.
    marker = "function setUserBody("
    start = src.index(marker)
    end = src.index("\n}\n", start)
    body = src[start:end]
    assert "code.textContent = text.trim()" not in body, (
        "setUserBody still bolds the whole message; only the leading "
        "slash token should be wrapped in <code class='slash-cmd'>."
    )
