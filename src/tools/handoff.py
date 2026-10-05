# src/tools/handoff.py
"""``handoff``: one-way collaboration between containers.

The sender POSTs a note plus the context it chooses to a peer container's
``/peer/handoff`` endpoint (``src/channels/peer.py``) and learns only whether
the handoff was delivered. The receiver answers *its* user on its own
``user_delivery`` channel; nothing ever comes back here. The tool is
registered only when the container's ``outbound`` list names a container,
and the executor re-checks that list on every call. See
``docs/superpowers/specs/2026-09-26-agents-and-containers-design.md``.
"""
import json
import logging
from uuid import uuid4

import httpx

from src.config import AgentConfig
from src.container import peer_token

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


async def exec_handoff(args: dict, config: AgentConfig, agent=None, on_tool_call=None) -> str:
    """Send a handoff to the container named in ``args['container']``."""
    target = (args.get("container") or "").strip()
    note = (args.get("note") or "").strip()
    context = args.get("context") or ""
    to_agent = (args.get("agent") or "").strip() or None
    if not target:
        return "refused: 'container' is required"
    if not note:
        return "refused: 'note' is required"

    container = getattr(agent, "container", None)
    manifest = getattr(container, "manifest", None)
    if manifest is None or target not in manifest.outbound_containers:
        allowed = ", ".join(manifest.outbound_containers) if manifest else ""
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
