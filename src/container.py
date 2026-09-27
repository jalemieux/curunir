"""Container manifest and runtime (agents-and-containers, phase 2).

A *container* is one curunir process hosting one or more *agents*. Each agent
is a persona bundle plus its own private context dir; the container root
``context/`` is the shared area. See
``docs/superpowers/specs/2026-09-26-agents-and-containers-design.md``.

Two entry points:

- :func:`load_container` reads ``container.yaml`` (selected with
  ``CURUNIR_CONTAINER=<path>``) and validates it at boot, raising on a
  malformed manifest exactly like :func:`src.persona.load_persona`.
- :func:`synthesize_container` builds the one-agent manifest that is today's
  deployment: the persona from ``CURUNIR_PERSONA`` with its context at the
  container root (``context: .``) and ``inbound``/``outbound`` of ``[user]``.

The inbound/outbound lists, ``peers`` and ``user_delivery`` are parsed and
structurally validated here so a manifest is forward-compatible, but
cross-container messaging (handoff, the peer channel) lands in phase 3; a
manifest that names another container boots with a warning.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Mapping

import yaml

from src.channels.base import IncomingMessage, OutgoingMessage
from src.config import AgentConfig
from src.persona import DEFAULT_PERSONA, load_persona

if TYPE_CHECKING:  # pragma: no cover - typing only
    from src.agent.agent import Agent

logger = logging.getLogger(__name__)

USER = "user"

# Fixed session ids that pre-date multi-agent routing. They belong to the
# default agent; every other agent's conversations use minted UUIDs so portal
# routing (keyed on session id) never sees a collision.
RESERVED_SESSION_IDS = frozenset({"portal", "local", "scratch"})


@dataclass(frozen=True)
class AgentEntry:
    name: str
    persona: str
    context: str            # relative to the container root; "." = the root itself
    default: bool = False
    description: str = ""   # the persona's description, for routing hints/UI

    def context_dir(self, root: Path) -> Path:
        """This agent's private context dir under the container root."""
        rel = Path(self.context)
        if rel.is_absolute():
            return rel
        return (root / rel) if self.context not in (".", "") else root


@dataclass(frozen=True)
class PeerEntry:
    url: str
    token_env: str


@dataclass(frozen=True)
class ContainerManifest:
    name: str
    agents: tuple[AgentEntry, ...]
    inbound: tuple[str, ...] = (USER,)
    outbound: tuple[str, ...] = (USER,)
    peers: dict[str, PeerEntry] = field(default_factory=dict)
    user_delivery: str | None = None
    source: Path | None = None  # manifest path, None when synthesized

    @property
    def default_agent(self) -> AgentEntry:
        return next(a for a in self.agents if a.default)

    @property
    def multi_agent(self) -> bool:
        return len(self.agents) > 1

    @property
    def agent_names(self) -> list[str]:
        return [a.name for a in self.agents]

    def agent(self, name: str) -> AgentEntry | None:
        return next((a for a in self.agents if a.name == name), None)

    def siblings_of(self, name: str) -> list[AgentEntry]:
        return [a for a in self.agents if a.name != name]

    @property
    def outbound_containers(self) -> list[str]:
        return [c for c in self.outbound if c != USER]

    @property
    def inbound_containers(self) -> list[str]:
        return [c for c in self.inbound if c != USER]


# --- loading ---------------------------------------------------------------

def synthesize_container(persona_name: str | None = None) -> ContainerManifest:
    """The one-agent container that is today's deployment.

    The agent is named after its persona, lives at the container root
    (``context: .``) and is the default. Both lists are ``[user]``.
    """
    persona_name = (persona_name or "").strip() or DEFAULT_PERSONA
    persona = load_persona(persona_name)
    entry = AgentEntry(
        name=persona_name, persona=persona_name, context=".",
        default=True, description=persona.description,
    )
    return ContainerManifest(name=persona_name, agents=(entry,))


def _as_list(value, what: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"container manifest: `{what}` must be a list")
    return value


def _parse_list(data: dict, key: str) -> tuple[str, ...]:
    if key not in data:
        return (USER,)
    items = _as_list(data.get(key), key)
    out: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"container manifest: `{key}` entries must be non-empty strings")
        out.append(item.strip())
    return tuple(out)


def load_container(path: Path | str, environ: Mapping[str, str] | None = None) -> ContainerManifest:
    """Load and validate a ``container.yaml``.

    Raises ``FileNotFoundError`` when the file is missing and ``ValueError``
    on any malformed content, so boot fails loudly rather than running with
    a half-understood layout.
    """
    environ = os.environ if environ is None else environ
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"container manifest not found: {path}")
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"container manifest {path} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"container manifest {path} must be a mapping")

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("container manifest: `name` is required")
    name = name.strip()

    raw_agents = _as_list(data.get("agents"), "agents")
    if not raw_agents:
        raise ValueError("container manifest: `agents` must list at least one agent")

    entries: list[AgentEntry] = []
    seen: set[str] = set()
    for raw in raw_agents:
        if isinstance(raw, str):
            raw = {"name": raw}
        if not isinstance(raw, dict):
            raise ValueError("container manifest: each agent must be a mapping or a name")
        agent_name = raw.get("name")
        if not isinstance(agent_name, str) or not agent_name.strip():
            raise ValueError("container manifest: every agent needs a `name`")
        agent_name = agent_name.strip()
        if "/" in agent_name or agent_name in (".", ".."):
            raise ValueError(f"container manifest: agent name {agent_name!r} may not contain '/'")
        if agent_name in seen:
            raise ValueError(f"container manifest: duplicate agent name {agent_name!r}")
        seen.add(agent_name)
        persona_name = str(raw.get("persona") or agent_name)
        try:
            persona = load_persona(persona_name)
        except FileNotFoundError as exc:
            raise ValueError(
                f"container manifest: agent {agent_name!r} names persona "
                f"{persona_name!r}, which does not exist"
            ) from exc
        context = raw.get("context")
        if context is None:
            context = f"agents/{agent_name}"
        if not isinstance(context, str) or not context.strip():
            raise ValueError(f"container manifest: agent {agent_name!r} has an invalid `context`")
        entries.append(AgentEntry(
            name=agent_name, persona=persona_name, context=context.strip(),
            default=bool(raw.get("default", False)), description=persona.description,
        ))

    defaults = [a for a in entries if a.default]
    if len(entries) == 1 and not defaults:
        entries = [AgentEntry(**{**entries[0].__dict__, "default": True})]
        defaults = entries
    if len(defaults) != 1:
        raise ValueError(
            "container manifest: exactly one agent must have `default: true` "
            f"(found {len(defaults)})"
        )
    contexts = [a.context for a in entries]
    if len(set(contexts)) != len(contexts):
        raise ValueError("container manifest: two agents share the same `context` dir")

    inbound = _parse_list(data, "inbound")
    outbound = _parse_list(data, "outbound")
    if USER not in inbound or USER not in outbound:
        raise ValueError("container manifest: `user` must appear in both `inbound` and `outbound`")

    raw_peers = data.get("peers") or {}
    if not isinstance(raw_peers, dict):
        raise ValueError("container manifest: `peers` must be a mapping")
    peers: dict[str, PeerEntry] = {}
    for peer_name, raw_peer in raw_peers.items():
        if not isinstance(raw_peer, dict) or not raw_peer.get("url") or not raw_peer.get("token_env"):
            raise ValueError(f"container manifest: peer {peer_name!r} needs `url` and `token_env`")
        peers[str(peer_name)] = PeerEntry(url=str(raw_peer["url"]), token_env=str(raw_peer["token_env"]))

    named = {c for c in (*inbound, *outbound) if c != USER}
    for container_name in sorted(named):
        peer = peers.get(container_name)
        if peer is None:
            raise ValueError(
                f"container manifest: {container_name!r} is in a list but has no `peers` entry"
            )
        if not environ.get(peer.token_env):
            raise ValueError(
                f"container manifest: peer {container_name!r} names token env "
                f"{peer.token_env!r}, which is not set"
            )
    if named:
        logger.warning(
            "container %s lists other containers (%s); cross-container "
            "messaging is not active yet in this build",
            name, ", ".join(sorted(named)),
        )

    user_delivery = data.get("user_delivery")
    if user_delivery is not None and (not isinstance(user_delivery, str) or not user_delivery.strip()):
        raise ValueError("container manifest: `user_delivery` must be a channel name")

    return ContainerManifest(
        name=name, agents=tuple(entries), inbound=inbound, outbound=outbound,
        peers=peers, user_delivery=user_delivery.strip() if user_delivery else None,
        source=path,
    )


def resolve_manifest(environ: Mapping[str, str] | None = None) -> ContainerManifest:
    """The manifest for this boot: ``CURUNIR_CONTAINER`` or a synthesized one."""
    environ = os.environ if environ is None else environ
    manifest_path = (environ.get("CURUNIR_CONTAINER") or "").strip()
    if manifest_path:
        return load_container(manifest_path, environ)
    return synthesize_container(environ.get("CURUNIR_PERSONA"))


def build_agent_config(entry: AgentEntry, root: Path, **overrides) -> AgentConfig:
    """An ``AgentConfig`` for one manifest entry.

    Private paths derive from the entry's context dir and shared paths from
    the container root (``AgentConfig.for_agent``); the persona sets the
    allowlist. ``overrides`` are the env-derived settings ``run.py`` applies
    to every agent (model, limits, vision, ...).
    """
    persona = load_persona(entry.persona)
    kwargs: dict = {"persona": entry.persona}
    if persona.skills:
        kwargs["skill_allowlist"] = persona.skills
    kwargs.update(overrides)
    return AgentConfig.for_agent(
        entry.name, entry.context_dir(root), root,
        is_default=entry.default, **kwargs,
    )


# --- runtime ---------------------------------------------------------------

class Container:
    """The live agents of one process plus their per-agent inbound queues."""

    def __init__(self, manifest: ContainerManifest, agents: dict[str, "Agent"]):
        missing = [a.name for a in manifest.agents if a.name not in agents]
        if missing:
            raise ValueError(f"container: no Agent built for {missing}")
        self.manifest = manifest
        self.agents = agents
        self.queues: dict[str, asyncio.Queue] = {name: asyncio.Queue() for name in agents}
        for agent in agents.values():
            agent.container = self

    @property
    def default_agent(self) -> "Agent":
        return self.agents[self.manifest.default_agent.name]

    @property
    def multi_agent(self) -> bool:
        return self.manifest.multi_agent

    def resolve(self, name: str | None) -> "Agent | None":
        """The agent a message addresses; ``None``/empty means the default."""
        if not name:
            return self.default_agent
        return self.agents.get(name)

    def request_cancel(self, session_id: str, agent: str | None = None) -> bool:
        """Cancel an in-flight turn; without ``agent`` every agent is tried."""
        if agent:
            target = self.agents.get(agent)
            return bool(target and target.request_cancel(session_id))
        return any(a.request_cancel(session_id) for a in self.agents.values())

    def describe(self) -> list[dict]:
        """``[{name, persona, description, default}]`` for hello/meta frames."""
        return [
            {
                "name": a.name, "persona": a.persona,
                "description": a.description, "default": a.default,
            }
            for a in self.manifest.agents
        ]


async def route_inbound(in_queue: asyncio.Queue, container: Container, out_queue: asyncio.Queue) -> None:
    """Mirror of ``route_outbound``: hand each inbound message to its agent's queue.

    ``msg.agent`` (``None`` → the default agent) selects the worker. An
    unknown agent, or a reserved fixed session id addressed to a non-default
    agent, gets an error reply and is not enqueued.
    """
    while True:
        msg: IncomingMessage = await in_queue.get()
        agent = container.resolve(msg.agent)
        error: str | None = None
        if agent is None:
            error = (
                f"Unknown agent {msg.agent!r}. This container has: "
                + ", ".join(container.manifest.agent_names)
            )
        elif (
            not agent.config.is_default
            and msg.session_id in RESERVED_SESSION_IDS
        ):
            error = (
                f"Session id {msg.session_id!r} is reserved for the default agent; "
                f"open a new conversation with agent {agent.config.agent_name!r}."
            )
        if error is not None:
            logger.warning("route_inbound: %s", error)
            await out_queue.put(OutgoingMessage(
                content=error, channel=msg.channel, session_id=msg.session_id,
                reply_address=msg.reply_address, agent=msg.agent,
            ))
            continue
        msg.agent = agent.config.agent_name
        await container.queues[agent.config.agent_name].put(msg)
