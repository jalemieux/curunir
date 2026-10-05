# src/tools/handoff.py
"""``handoff``: a one-way transfer to another agent.

Two kinds of target, one payload (a note plus the context the sender chooses;
never the transcript) and one result (``delivered`` / ``refused: <reason>``):

- **Another container.** The sender POSTs to the peer's ``/peer/handoff``
  endpoint (``src/channels/peer.py``). The receiver answers *its* user on its
  own ``user_delivery`` channel; nothing ever comes back here. Offered only
  for containers the ``outbound`` list names, re-checked on every call.
- **A sibling agent in this container.** In process: the wrapped handoff is
  put on the sibling's queue as a new ``handoff:<id>`` conversation on the
  channel the user is on. No HTTP hop and no token (siblings already
  collaborate through ``ask_agent``). The sibling's worker persists and
  extracts that conversation like any other.

``ask_agent`` is the other verb: a consult whose answer returns to the asker.
See ``docs/superpowers/specs/2026-09-26-agents-and-containers-design.md``.
"""
import json
import logging
from uuid import uuid4

import httpx

from src.channels.base import IncomingMessage
from src.channels.peer import SESSION_PREFIX, wrap_sibling_handoff
from src.config import AgentConfig
from src.container import peer_token
from src.turn_context import current_turn

logger = logging.getLogger(__name__)

# Must match the receiver's cap (src/channels/peer.py); checked here too so an
# oversized handoff is refused without a round trip.
MAX_PAYLOAD_BYTES = 256 * 1024

_TIMEOUT = 30.0

# The receiver's status code is mapped to a fixed reason. The response body
# is never read, so a peer cannot pass anything back through this tool.
_REFUSALS = {
    400: "the receiver rejected the request as malformed",
    401: "the receiver does not recognize this container's token",
    403: "this container is not in the receiver's inbound list",
    422: (
        "the receiver has no agent by that name; omit 'agent' to reach its "
        "default agent"
    ),
    413: "the handoff is larger than the receiver accepts",
}


# Channels whose client can switch agents and open the receiver's
# conversation. Email and the portal send every message to the default agent,
# so a sibling's reply there would strand the user's follow-ups.
SIBLING_HANDOFF_CHANNELS = frozenset({"local_web"})

AGENT_PREFIX = "agent:"
CONTAINER_PREFIX = "container:"


def _resolve_target(args: dict, siblings: list[str], containers: list[str]) -> tuple[str, str]:
    """``(kind, name)`` for the call's target; kind is ``agent``/``container``/``""``.

    ``to`` carries a prefixed value from the schema enum (``agent:<name>`` or
    ``container:<name>``). A bare name is accepted when it is unambiguous, and
    the pre-sibling ``container`` argument still names a container.
    """
    to = (args.get("to") or "").strip()
    if not to:
        legacy = (args.get("container") or "").strip()
        return ("container", legacy) if legacy else ("", "")
    if to.startswith(AGENT_PREFIX):
        return "agent", to[len(AGENT_PREFIX):].strip()
    if to.startswith(CONTAINER_PREFIX):
        return "container", to[len(CONTAINER_PREFIX):].strip()
    if to in siblings and to not in containers:
        return "agent", to
    if to in containers and to not in siblings:
        return "container", to
    return "", to


async def _handoff_to_sibling(
    name: str, note: str, context: str, config: AgentConfig, sender,
) -> str:
    """Open a ``handoff:<id>`` conversation with sibling ``name``."""
    turn = current_turn.get()
    if turn is None:
        return (
            "refused: there is no user conversation to transfer (this is not "
            "a user turn). Use ask_agent if you need the sibling's answer."
        )
    if turn.channel not in SIBLING_HANDOFF_CHANNELS:
        return (
            f"refused: the user is on the {turn.channel!r} channel, which "
            "cannot switch to another agent's conversation. Answer yourself, "
            "or use ask_agent and relay the answer."
        )
    # A handoff the receiver passes straight on, before the user has said
    # anything in it, could bounce between agents with nobody watching.
    if turn.session_id.startswith(SESSION_PREFIX):
        history = sender.sessions.get(turn.session_id, [])
        if sum(1 for m in history if m.get("role") == "user") <= 1:
            return (
                "refused: this conversation was itself just handed to you. "
                "Answer the user first; do not pass it on unanswered."
            )
    size = len(note.encode("utf-8")) + len(context.encode("utf-8"))
    if size > MAX_PAYLOAD_BYTES:
        return (
            f"refused: the handoff is {size // 1024} KB; the limit is "
            f"{MAX_PAYLOAD_BYTES // 1024} KB. Send a shorter context."
        )

    handoff_id = uuid4().hex
    session_id = f"{SESSION_PREFIX}{handoff_id}"
    await sender.container.queues[name].put(IncomingMessage(
        content=wrap_sibling_handoff(config.agent_name, note, context),
        channel=turn.channel,
        session_id=session_id,
        reply_address=dict(turn.reply_address),
        agent=name,
    ))
    turn.handoffs.append({"agent": name, "session_id": session_id})
    logger.info(
        "handoff %s -> sibling %s delivered (%s) on %s",
        config.agent_name, name, handoff_id, turn.channel,
    )
    return "delivered"


async def exec_handoff(args: dict, config: AgentConfig, agent=None, on_tool_call=None) -> str:
    """Hand off to the sibling agent or container named in ``args['to']``."""
    note = (args.get("note") or "").strip()
    context = args.get("context") or ""
    to_agent = (args.get("agent") or "").strip() or None

    container = getattr(agent, "container", None)
    manifest = getattr(container, "manifest", None)
    siblings = (
        [a.name for a in manifest.siblings_of(config.agent_name)] if manifest else []
    )
    containers = manifest.outbound_containers if manifest else []
    kind, target = _resolve_target(args, siblings, containers)
    if not target:
        return "refused: 'to' is required"
    if not note:
        return "refused: 'note' is required"

    if kind == "agent":
        if target not in siblings:
            return (
                f"refused: {target!r} is not a sibling agent in this container"
                + (f" (siblings: {', '.join(siblings)})" if siblings else "")
            )
        return await _handoff_to_sibling(target, note, context, config, agent)
    if kind != "container":
        options = [AGENT_PREFIX + n for n in siblings] + [CONTAINER_PREFIX + c for c in containers]
        return (
            f"refused: {target!r} does not name one target"
            + (f" (use one of: {', '.join(options)})" if options else "")
        )

    if manifest is None or target not in containers:
        allowed = ", ".join(containers)
        return (
            f"refused: {target!r} is not in this container's outbound list"
            + (f" (allowed: {allowed})" if allowed else "")
        )
    token = peer_token(manifest, target)
    if not token:
        return f"refused: no shared secret is configured for {target!r}"

    handoff_id = uuid4().hex
    payload = {
        "handoff_id": handoff_id,
        "from_container": manifest.name,
        "from_agent": config.agent_name,
        "to_agent": to_agent,
        "note": note,
        "context": context,
    }
    body = json.dumps(payload).encode("utf-8")
    if len(body) > MAX_PAYLOAD_BYTES:
        return (
            f"refused: the handoff is {len(body) // 1024} KB; the limit is "
            f"{MAX_PAYLOAD_BYTES // 1024} KB. Send a shorter context."
        )

    url = manifest.peers[target].url.rstrip("/") + "/peer/handoff"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                url, content=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
    except httpx.HTTPError as e:
        logger.warning("handoff %s -> %s failed: %s", manifest.name, target, e)
        return f"refused: could not reach container {target!r}"

    if 200 <= resp.status_code < 300:
        logger.info(
            "handoff %s/%s -> %s delivered (%s)",
            manifest.name, config.agent_name, target, handoff_id,
        )
        return "delivered"
    reason = _REFUSALS.get(resp.status_code, f"the receiver answered HTTP {resp.status_code}")
    logger.warning("handoff %s -> %s refused: HTTP %s", manifest.name, target, resp.status_code)
    return f"refused: {reason}"
