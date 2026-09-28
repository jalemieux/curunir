"""Container manifest, runtime and inbound routing (agents-and-containers, phase 2)."""
import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.channels.base import IncomingMessage
from src.config import AgentConfig
from src.container import (
    RESERVED_SESSION_IDS,
    AgentEntry,
    Container,
    ContainerManifest,
    build_agent_config,
    load_container,
    resolve_manifest,
    route_inbound,
    synthesize_container,
)


@pytest.fixture
def personas(tmp_path, monkeypatch):
    """personas/{default,finance,marketing} under a temp PERSONAS_DIR."""
    root = tmp_path / "personas"
    monkeypatch.setattr("src.persona.PERSONAS_DIR", root)
    for name, skills in (("default", None), ("finance", ["balance-sheet", "identity"]), ("marketing", ["crm"])):
        d = root / name
        d.mkdir(parents=True)
        body = f"name: {name}\ndescription: the {name} persona\n"
        if skills:
            body += "skills:\n" + "".join(f"  - {s}\n" for s in skills)
        (d / "persona.yaml").write_text(body)
    return root


def _write(tmp_path, text: str) -> Path:
    p = tmp_path / "container.yaml"
    p.write_text(text)
    return p


TWO_AGENTS = """
name: home
agents:
  - name: everyday
    persona: default
    context: .
    default: true
  - name: finance
"""


# --- synthesize / load -------------------------------------------------------

def test_synthesize_is_the_legacy_single_agent_layout(personas):
    m = synthesize_container("finance")
    assert m.name == "finance"
    assert [a.name for a in m.agents] == ["finance"]
    a = m.default_agent
    assert a.persona == "finance" and a.context == "." and a.default
    assert a.description == "the finance persona"
    assert m.inbound == ("user",) and m.outbound == ("user",)
    assert not m.multi_agent and m.source is None


def test_synthesize_defaults_to_the_default_persona(personas):
    assert synthesize_container(None).default_agent.persona == "default"
    assert synthesize_container("  ").default_agent.persona == "default"


def test_load_two_agent_manifest(personas, tmp_path):
    m = load_container(_write(tmp_path, TWO_AGENTS))
    assert m.name == "home" and m.multi_agent
    assert m.agent_names == ["everyday", "finance"]
    everyday, finance = m.agents
    assert everyday.default and everyday.context == "."
    # persona defaults to the agent name; context defaults to agents/<name>
    assert finance.persona == "finance" and finance.context == "agents/finance"
    assert not finance.default and finance.description == "the finance persona"
    assert [a.name for a in m.siblings_of("everyday")] == ["finance"]
    assert m.source == tmp_path / "container.yaml"


def test_context_dir_resolution(personas, tmp_path):
    m = load_container(_write(tmp_path, TWO_AGENTS))
    root = Path("./context")
    assert m.agent("everyday").context_dir(root) == root
    assert m.agent("finance").context_dir(root) == root / "agents" / "finance"


def test_single_agent_manifest_implies_default(personas, tmp_path):
    m = load_container(_write(tmp_path, "name: solo\nagents:\n  - name: finance\n"))
    assert m.default_agent.name == "finance"


def test_agent_may_be_a_bare_name(personas, tmp_path):
    m = load_container(_write(tmp_path, "name: solo\nagents: [finance]\n"))
    assert m.default_agent.persona == "finance"


@pytest.mark.parametrize("yaml_text, message", [
    ("agents:\n  - name: finance\n", "`name` is required"),
    ("name: x\n", "at least one agent"),
    ("name: x\nagents: []\n", "at least one agent"),
    ("name: x\nagents:\n  - name: a\n    persona: finance\n  - name: a\n    persona: default\n", "duplicate agent name"),
    ("name: x\nagents:\n  - name: finance\n  - name: marketing\n", "exactly one agent must have `default: true` (found 0)"),
    ("name: x\nagents:\n  - name: finance\n    default: true\n  - name: marketing\n    default: true\n", "(found 2)"),
    ("name: x\nagents:\n  - name: ghost\n", "persona 'ghost', which does not exist"),
    ("name: x\nagents:\n  - name: a\n    persona: finance\n    context: same\n    default: true\n  - name: b\n    persona: default\n    context: same\n", "share the same `context`"),
    ("name: x\nagents: [finance]\ninbound: [vault]\n", "`user` must appear in both"),
    ("name: x\nagents: [finance]\noutbound: [user, vault]\n", "'vault' is in a list but has no `peers` entry"),
    ("name: x\nagents: [finance]\noutbound: [user, vault]\npeers:\n  vault:\n    url: http://v\n    token_env: PEER_VAULT_TOKEN\n", "token env 'PEER_VAULT_TOKEN', which is not set"),
    ("name: x\nagents: [finance]\npeers:\n  vault:\n    url: http://v\n", "peer 'vault' needs `url` and `token_env`"),
    ("name: x\nagents: [finance]\nuser_delivery: cli\n", "`user_delivery` must be one of email, local_web, portal"),
    ("name: x\nagents:\n  - name: a/b\n    persona: finance\n", "may not contain '/'"),
    ("- just\n- a list\n", "must be a mapping"),
])
def test_manifest_validation_errors(personas, tmp_path, yaml_text, message):
    with pytest.raises(ValueError, match=__import__("re").escape(message)):
        load_container(_write(tmp_path, yaml_text), environ={})


def test_peer_with_token_set_is_accepted(personas, tmp_path, caplog):
    text = (
        "name: x\nagents: [finance]\noutbound: [user, vault]\n"
        "peers:\n  vault:\n    url: http://vault:8767\n    token_env: PEER_VAULT_TOKEN\n"
        "user_delivery: portal\n"
    )
    with caplog.at_level("WARNING"):
        m = load_container(_write(tmp_path, text), environ={"PEER_VAULT_TOKEN": "s3cret"})
    assert m.outbound_containers == ["vault"] and m.inbound_containers == []
    assert m.peers["vault"].url == "http://vault:8767"
    assert m.user_delivery == "portal"
    assert "not active yet" not in caplog.text


def test_missing_manifest_file_raises(personas, tmp_path):
    with pytest.raises(FileNotFoundError):
        load_container(tmp_path / "nope.yaml")


def test_invalid_yaml_raises_value_error(personas, tmp_path):
    with pytest.raises(ValueError, match="not valid YAML"):
        load_container(_write(tmp_path, "name: [unclosed\n"))


def test_resolve_manifest_prefers_container_env(personas, tmp_path):
    path = _write(tmp_path, TWO_AGENTS)
    m = resolve_manifest({"CURUNIR_CONTAINER": str(path), "CURUNIR_PERSONA": "marketing"})
    assert m.name == "home"
    m2 = resolve_manifest({"CURUNIR_PERSONA": "marketing"})
    assert m2.default_agent.persona == "marketing" and not m2.multi_agent


# --- build_agent_config --------------------------------------------------------

def test_build_agent_config_legacy_entry_equals_bare_config(personas):
    entry = synthesize_container("default").default_agent
    cfg = build_agent_config(entry, Path("./context"))
    bare = AgentConfig()
    for f in ("context_dir", "shared_dir", "identity_file", "schedules_db", "portfolio_db",
              "crm_db", "usage_db", "skill_dirs", "agent_name", "is_default", "persona"):
        assert getattr(cfg, f) == getattr(bare, f), f
    assert cfg.skill_allowlist is None


def test_build_agent_config_for_a_sibling(personas, tmp_path):
    m = load_container(_write(tmp_path, TWO_AGENTS))
    root = tmp_path / "context"
    cfg = build_agent_config(m.agent("finance"), root, model="openai/gpt-4o")
    assert cfg.agent_name == "finance" and cfg.is_default is False
    assert cfg.persona == "finance" and cfg.skill_allowlist == ["balance-sheet", "identity"]
    assert cfg.context_dir == root / "agents" / "finance"
    assert cfg.shared_dir == root
    assert cfg.usage_db == root / "usage.db"
    assert cfg.model == "openai/gpt-4o"


# --- runtime + routing ---------------------------------------------------------

def _fake_agent(name: str, default: bool) -> MagicMock:
    a = MagicMock()
    a.config = AgentConfig.for_agent(name, Path("/tmp/x") / name, Path("/tmp/x"), is_default=default)
    a.request_cancel = MagicMock(return_value=False)
    return a


def _container() -> Container:
    manifest = ContainerManifest(
        name="home",
        agents=(
            AgentEntry("everyday", "default", ".", True, "generalist"),
            AgentEntry("finance", "finance", "agents/finance", False, "money"),
        ),
    )
    return Container(manifest, {"everyday": _fake_agent("everyday", True), "finance": _fake_agent("finance", False)})


def test_container_sets_back_reference_and_resolves():
    c = _container()
    assert c.multi_agent
    assert c.default_agent is c.agents["everyday"]
    assert c.agents["finance"].container is c
    assert c.resolve(None) is c.default_agent
    assert c.resolve("") is c.default_agent
    assert c.resolve("finance") is c.agents["finance"]
    assert c.resolve("nope") is None
    assert set(c.queues) == {"everyday", "finance"}


def test_container_requires_an_agent_per_entry():
    manifest = ContainerManifest(name="x", agents=(AgentEntry("a", "default", ".", True),))
    with pytest.raises(ValueError, match="no Agent built"):
        Container(manifest, {})


def test_container_describe():
    assert _container().describe() == [
        {"name": "everyday", "persona": "default", "description": "generalist", "default": True},
        {"name": "finance", "persona": "finance", "description": "money", "default": False},
    ]


def test_request_cancel_targets_named_agent_or_tries_all():
    c = _container()
    c.agents["finance"].request_cancel.return_value = True
    assert c.request_cancel("s1", agent="finance") is True
    assert c.request_cancel("s1", agent="everyday") is False
    assert c.request_cancel("s1", agent="ghost") is False
    assert c.request_cancel("s1") is True  # any agent
    c.agents["finance"].request_cancel.return_value = False
    assert c.request_cancel("s1") is False


def _msg(agent, sid="abc"):
    return IncomingMessage(content="hi", channel="cli", session_id=sid, reply_address={}, agent=agent)


async def _route_one(c: Container, msg: IncomingMessage):
    in_q, out_q = asyncio.Queue(), asyncio.Queue()
    await in_q.put(msg)
    task = asyncio.create_task(route_inbound(in_q, c, out_q))
    await asyncio.sleep(0.01)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return out_q


@pytest.mark.asyncio
async def test_route_inbound_default_agent_when_field_missing():
    c = _container()
    await _route_one(c, _msg(None))
    routed = c.queues["everyday"].get_nowait()
    assert routed.agent == "everyday"  # canonical name stamped
    assert c.queues["finance"].empty()


@pytest.mark.asyncio
async def test_route_inbound_named_agent():
    c = _container()
    await _route_one(c, _msg("finance"))
    assert c.queues["finance"].get_nowait().agent == "finance"
    assert c.queues["everyday"].empty()


@pytest.mark.asyncio
async def test_route_inbound_unknown_agent_gets_error_reply_and_no_enqueue():
    c = _container()
    out_q = await _route_one(c, _msg("ghost", sid="s9"))
    reply = out_q.get_nowait()
    assert "Unknown agent 'ghost'" in reply.content and "everyday, finance" in reply.content
    assert reply.session_id == "s9" and reply.channel == "cli" and reply.agent == "ghost"
    assert c.queues["everyday"].empty() and c.queues["finance"].empty()


@pytest.mark.asyncio
@pytest.mark.parametrize("sid", sorted(RESERVED_SESSION_IDS))
async def test_route_inbound_reserved_session_ids_belong_to_the_default_agent(sid):
    c = _container()
    out_q = await _route_one(c, _msg("finance", sid=sid))
    reply = out_q.get_nowait()
    assert "reserved for the default agent" in reply.content
    assert c.queues["finance"].empty()
    # ...but the default agent may use them
    await _route_one(c, _msg(None, sid=sid))
    assert c.queues["everyday"].get_nowait().session_id == sid
