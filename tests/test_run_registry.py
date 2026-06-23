"""Multi-tenant registry + dispatcher (#420).

One process hosts many personas: build_registry mints one AgentRuntime per
persona, each with its own context_dir (so ALL per-persona state forks) and a
jailed FS sandbox. The agent_worker becomes a dispatcher that routes each
inbound message to registry[msg.persona].
"""
import asyncio
from pathlib import Path

import pytest

from src.channels.base import IncomingMessage
from src.config import AgentConfig
from src.runtime import (
    AgentRuntime,
    build_email_configs,
    build_registry,
    parse_personas,
)


class _FakeAgent:
    """Minimal Agent stand-in that records handled messages."""

    def __init__(self, config):
        self.config = config
        self.sessions = {}
        self.handled = []

    async def handle(self, content, session_id, **kwargs):
        self.handled.append((session_id, content))
        return f"[{self.config.persona}] ok"

    def request_cancel(self, session_id):
        pass


def _build(tmp_path, names):
    return build_registry(
        names,
        base_context=tmp_path / "context",
        config_overrides={"model": "anthropic/claude-sonnet-4-20250514"},
        agent_factory=lambda cfg, usage_store: _FakeAgent(cfg),
        usage_store_factory=lambda cfg: None,
    )


def test_registry_builds_one_runtime_per_persona(tmp_path):
    reg = _build(tmp_path, ["default", "finance"])
    assert set(reg) == {"default", "finance"}
    assert isinstance(reg["default"], AgentRuntime)


def test_each_runtime_has_isolated_context_dir(tmp_path):
    reg = _build(tmp_path, ["default", "finance"])
    d_ctx = reg["default"].config.context_dir
    f_ctx = reg["finance"].config.context_dir
    assert d_ctx != f_ctx
    assert d_ctx == (tmp_path / "context" / "default")
    assert f_ctx == (tmp_path / "context" / "finance")
    # Every state path forks onto the persona root.
    assert reg["finance"].config.usage_db == f_ctx / "usage.db"
    assert reg["finance"].config.schedules_db == f_ctx / "schedules.db"


def test_each_runtime_is_fs_jailed_to_its_workspace(tmp_path):
    reg = _build(tmp_path, ["default", "finance"])
    for name, rt in reg.items():
        assert rt.config.fs_jail is True
        assert rt.config.workdir == rt.config.context_dir / "workspace"
        # The sandbox root is provisioned on disk so the jail can contain it.
        assert rt.config.workdir.is_dir()


def test_finance_persona_keeps_its_skill_allowlist(tmp_path):
    reg = _build(tmp_path, ["finance"])
    # finance/persona.yaml ships an allowlist; default does not.
    assert reg["finance"].config.skill_allowlist is not None
    assert "balance-sheet" in reg["finance"].config.skill_allowlist


@pytest.mark.asyncio
async def test_agent_worker_dispatches_by_persona(tmp_path):
    from run import agent_worker

    reg = _build(tmp_path, ["default", "finance"])
    in_q = asyncio.Queue()
    out_q = asyncio.Queue()
    worker = asyncio.create_task(agent_worker(reg, in_q, out_q))

    await in_q.put(IncomingMessage(
        content="hi finance", channel="cli", session_id="s1",
        reply_address={}, persona="finance",
    ))
    await in_q.put(IncomingMessage(
        content="hi default", channel="cli", session_id="s2",
        reply_address={}, persona="default",
    ))

    out1 = await asyncio.wait_for(out_q.get(), timeout=5)
    out2 = await asyncio.wait_for(out_q.get(), timeout=5)
    worker.cancel()

    # Each message reached the matching runtime's agent.
    assert reg["finance"].agent.handled == [("s1", "hi finance")]
    assert reg["default"].agent.handled == [("s2", "hi default")]
    outs = {o.session_id: o for o in (out1, out2)}
    assert outs["s1"].content == "[finance] ok"
    assert outs["s2"].content == "[default] ok"


@pytest.mark.asyncio
async def test_agent_worker_falls_back_for_unknown_persona(tmp_path):
    """An unrecognized/blank persona resolves to the default runtime rather
    than dropping the message (legacy channels never set persona)."""
    from run import agent_worker

    reg = _build(tmp_path, ["default"])
    in_q = asyncio.Queue()
    out_q = asyncio.Queue()
    worker = asyncio.create_task(agent_worker(reg, in_q, out_q))

    await in_q.put(IncomingMessage(
        content="orphan", channel="cli", session_id="s9",
        reply_address={}, persona="does-not-exist",
    ))
    out = await asyncio.wait_for(out_q.get(), timeout=5)
    worker.cancel()
    assert reg["default"].agent.handled == [("s9", "orphan")]
    assert out.content == "[default] ok"


def test_parse_personas_prefers_personas_list():
    assert parse_personas({"CURUNIR_PERSONAS": "default, finance ,finance"}) == [
        "default", "finance",
    ]


def test_parse_personas_falls_back_to_single():
    assert parse_personas({"CURUNIR_PERSONA": "finance"}) == ["finance"]
    assert parse_personas({}) == ["default"]


def test_build_email_configs_single_persona_uses_global_vars(tmp_path):
    reg = _build(tmp_path, ["default"])
    env = {"FASTMAIL_USER": "jac@curunir.ai", "FASTMAIL_PASSWORD": "pw"}
    cfgs = build_email_configs(env, reg)
    assert set(cfgs) == {"default"}
    assert cfgs["default"].user == "jac@curunir.ai"
    # State file lives under the persona's own context root.
    assert cfgs["default"].state_file == reg["default"].config.context_dir / "email_state.json"


def test_build_email_configs_multi_persona_requires_suffixed_creds(tmp_path):
    reg = _build(tmp_path, ["default", "finance"])
    # Only finance has suffixed creds; the bare vars are NOT shared in multi mode.
    env = {
        "FASTMAIL_USER": "shared@curunir.ai",
        "FASTMAIL_PASSWORD": "shared",
        "FASTMAIL_USER__FINANCE": "finance@curunir.ai",
        "FASTMAIL_PASSWORD__FINANCE": "fpw",
    }
    cfgs = build_email_configs(env, reg)
    assert set(cfgs) == {"finance"}
    assert cfgs["finance"].user == "finance@curunir.ai"


@pytest.mark.asyncio
async def test_router_disambiguates_email_by_persona():
    from src.channels.base import OutgoingMessage
    from src.channels.router import route_outbound

    sent = {}

    class _Ch:
        def __init__(self, name):
            self.name = name

        async def send(self, msg):
            sent[self.name] = msg

    channels = {
        "email:default": _Ch("email:default"),
        "email:finance": _Ch("email:finance"),
        "cli": _Ch("cli"),
    }
    out_q = asyncio.Queue()
    task = asyncio.create_task(route_outbound(out_q, channels))

    await out_q.put(OutgoingMessage(
        content="x", channel="email", session_id="s", reply_address={},
        persona="finance",
    ))
    # Bare-name fallback for a single multiplexed channel.
    await out_q.put(OutgoingMessage(
        content="y", channel="cli", session_id="s", reply_address={},
        persona="finance",
    ))
    for _ in range(50):
        if "email:finance" in sent and "cli" in sent:
            break
        await asyncio.sleep(0.01)
    task.cancel()

    assert sent["email:finance"].content == "x"
    assert "email:default" not in sent
    assert sent["cli"].content == "y"
