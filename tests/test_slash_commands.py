"""Tests for src/slash_commands.py — the two-layer slash command dispatcher.

Layer 1: explicit intercepted registry (help, skills, clear) for utility
commands.
Layer 2: skill-forcing fallback that rewrites unknown `/<name>` to a
synthetic user prompt when *name* matches a registered skill, else replies
with a polite error.
"""

import pytest

from src.channels.base import IncomingMessage
from src.slash_commands import SlashContext, SlashResult, maybe_handle_slash


def _write_skill(skills_dir, name: str, description: str = "test skill") -> None:
    """Drop a minimal SKILL.md into *skills_dir* under name/SKILL.md."""
    skill_dir = skills_dir / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\nbody.\n"
    )


def _ctx(tmp_skills) -> SlashContext:
    return SlashContext(
        args="",
        session_id="cli",
        channel="cli",
        reply_address={},
        skill_dirs=[tmp_skills],
    )


@pytest.mark.asyncio
async def test_non_slash_returns_none(tmp_skills):
    result = await maybe_handle_slash("hello there", None, _ctx(tmp_skills))
    assert result is None


@pytest.mark.asyncio
async def test_empty_returns_none(tmp_skills):
    result = await maybe_handle_slash("", None, _ctx(tmp_skills))
    assert result is None


@pytest.mark.asyncio
async def test_attachments_falls_through(tmp_skills):
    """v1: slash commands don't take attachments — caller proceeds normally."""
    result = await maybe_handle_slash(
        "/help",
        [{"filename": "a.png", "mime_type": "image/png", "size": 1, "path": "/tmp/a"}],
        _ctx(tmp_skills),
    )
    assert result is None


@pytest.mark.asyncio
async def test_unknown_command_returns_polite_error(tmp_skills):
    result = await maybe_handle_slash("/nope", None, _ctx(tmp_skills))
    assert result is not None
    assert result.handled is True
    assert len(result.outgoing) == 1
    assert "Unknown command" in result.outgoing[0].content
    assert "/nope" in result.outgoing[0].content
    assert "/help" in result.outgoing[0].content
    assert result.outgoing[0].final is True
    assert result.enqueue == []


@pytest.mark.asyncio
async def test_help_lists_intercepted_and_skills(tmp_skills):
    _write_skill(tmp_skills, "yfinance", "stock quotes")
    _write_skill(tmp_skills, "fred", "macro data")

    result = await maybe_handle_slash("/help", None, _ctx(tmp_skills))
    assert result is not None
    assert result.handled is True
    assert len(result.outgoing) == 1
    body = result.outgoing[0].content
    # Intercepted commands
    for name in ("clear", "help", "skills"):
        assert name in body, f"expected /{name} in help table:\n{body}"
    # Skills from the registry
    assert "yfinance" in body
    assert "fred" in body
    # Cancellation hint — slash can't cancel; the channel does it out-of-band.
    assert "Ctrl-C" in body or "stop button" in body


@pytest.mark.asyncio
async def test_help_omits_cancel_command(tmp_skills):
    """Regression: /cancel was removed from the intercepted registry because
    it can't work through the queue (agent_worker is blocked during a turn).
    /help should not advertise it as a command."""
    result = await maybe_handle_slash("/help", None, _ctx(tmp_skills))
    body = result.outgoing[0].content
    assert "/cancel" not in body


@pytest.mark.asyncio
async def test_cancel_is_unknown_command(tmp_skills):
    """/cancel is no longer intercepted; it falls through to the unknown
    command handler (or skill-forcing, if a skill happens to be named that)."""
    result = await maybe_handle_slash("/cancel", None, _ctx(tmp_skills))
    assert result is not None
    assert len(result.outgoing) == 1
    assert "Unknown command" in result.outgoing[0].content


@pytest.mark.asyncio
async def test_skills_lists_registry(tmp_skills):
    _write_skill(tmp_skills, "alpha")
    _write_skill(tmp_skills, "beta")

    result = await maybe_handle_slash("/skills", None, _ctx(tmp_skills))
    assert result is not None
    assert result.handled is True
    assert len(result.outgoing) == 1
    body = result.outgoing[0].content
    assert "alpha" in body
    assert "beta" in body


@pytest.mark.asyncio
async def test_clear_emits_clear_command(tmp_skills):
    result = await maybe_handle_slash("/clear", None, _ctx(tmp_skills))
    assert result is not None
    assert result.handled is True
    assert result.outgoing == []
    assert len(result.enqueue) == 1
    inc = result.enqueue[0]
    assert isinstance(inc, IncomingMessage)
    assert inc.command == "clear"
    assert inc.session_id == "cli"


@pytest.mark.asyncio
async def test_new_and_reset_alias_clear(tmp_skills):
    for alias in ("/new", "/reset"):
        result = await maybe_handle_slash(alias, None, _ctx(tmp_skills))
        assert result is not None
        assert result.handled is True
        assert len(result.enqueue) == 1
        assert result.enqueue[0].command == "clear"


@pytest.mark.asyncio
async def test_skill_name_falls_through_to_skill_forcing(tmp_skills):
    _write_skill(tmp_skills, "yfinance", "stock data")
    result = await maybe_handle_slash(
        "/yfinance LLY price", None, _ctx(tmp_skills),
    )
    assert result is not None
    assert result.handled is True
    assert result.outgoing == []
    assert len(result.enqueue) == 1
    inc = result.enqueue[0]
    assert isinstance(inc, IncomingMessage)
    assert inc.command is None
    assert "yfinance" in inc.content
    assert "skill" in inc.content.lower()
    assert "LLY price" in inc.content


@pytest.mark.asyncio
async def test_skill_name_no_args(tmp_skills):
    """`/foo` with no args still produces a clean synthetic prompt."""
    _write_skill(tmp_skills, "identity")
    result = await maybe_handle_slash("/identity", None, _ctx(tmp_skills))
    assert result is not None
    assert len(result.enqueue) == 1
    text = result.enqueue[0].content
    assert "identity" in text
    # No trailing whitespace from f-string template.
    assert text == text.strip()


@pytest.mark.asyncio
async def test_intercepted_takes_precedence_over_skill_with_same_name(tmp_skills):
    """If a skill named `help` exists, `/help` still hits the intercepted handler."""
    _write_skill(tmp_skills, "help", "shadowed by intercept")
    result = await maybe_handle_slash("/help", None, _ctx(tmp_skills))
    assert result is not None
    # Intercepted /help returns an outgoing message (the help table), not an
    # enqueued synthetic prompt.
    assert len(result.outgoing) == 1
    assert result.enqueue == []


@pytest.mark.asyncio
async def test_outgoing_uses_context_channel_and_session(tmp_skills):
    """Outgoing replies carry the channel + session from SlashContext."""
    ctx = SlashContext(
        args="", session_id="tab-A", channel="portal",
        reply_address={"thread": "x"}, skill_dirs=[tmp_skills],
    )
    result = await maybe_handle_slash("/nope", None, ctx)
    assert result is not None
    out = result.outgoing[0]
    assert out.channel == "portal"
    assert out.session_id == "tab-A"
    assert out.reply_address == {"thread": "x"}


@pytest.mark.asyncio
async def test_enqueue_uses_context_channel_and_session(tmp_skills):
    _write_skill(tmp_skills, "foo")
    ctx = SlashContext(
        args="", session_id="tab-A", channel="portal",
        reply_address={"x": 1}, skill_dirs=[tmp_skills],
    )
    result = await maybe_handle_slash("/foo do thing", None, ctx)
    assert result is not None
    inc = result.enqueue[0]
    assert inc.channel == "portal"
    assert inc.session_id == "tab-A"
    assert inc.reply_address == {"x": 1}


@pytest.mark.asyncio
async def test_slash_result_defaults():
    r = SlashResult(handled=True)
    assert r.outgoing == []
    assert r.enqueue == []
