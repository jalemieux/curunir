"""Curunir runtime — configures channels, wires queues, starts the agent loop."""

import asyncio
import base64
import json
import logging
import logging.handlers
import os
import secrets
from pathlib import Path

import time

import httpx
import litellm
from dotenv import load_dotenv

from src.agent import conversation_store
from src.agent.agent import Agent
from src.agent.scratch import SCRATCH_SESSION_ID, is_scratch
from src.channels.base import OutgoingMessage
from src.channels.email import EmailChannel
from src.channels.local_web import LocalWebChannel
from src.channels.portal import PortalChannel
from src.channels.ws import WebSocketChannel
from src.channels.router import route_outbound
from src.config import AgentConfig, EmailChannelConfig, LocalWebConfig
from src.persona import DEFAULT_PERSONA, load_persona, warn_missing_keys
from onboarding.bootstrap import bootstrap_context
from src.document_ingest import ingest_document
from src.document_text import docx_to_text_block, pdf_to_text_block
from src.llm import describe_image
from src.memory_extractor import extract_learnings
from src.schedule_store import db as schedule_db
from src.scheduler import run_scheduler
from src.skills import load_skill, portal_skill_list
from src.slash_commands import SlashContext, maybe_handle_slash
from src.usage_store import UsageStore


def _summarize_tool_call(name: str, args_str: str) -> str:
    """Format a tool call for display, e.g. 'Read src/config.py'."""
    try:
        args = json.loads(args_str)
    except json.JSONDecodeError:
        return name

    match name:
        case "read":
            return f"Read {args.get('file_path', '')}"
        case "write":
            return f"Write {args.get('file_path', '')}"
        case "edit":
            return f"Edit {args.get('file_path', '')}"
        case "glob":
            return f"Glob {args.get('pattern', '')}"
        case "grep":
            return f"Grep pattern={args.get('pattern', '')!r}"
        case "bash":
            cmd = args.get("command", "")
            if len(cmd) > 60:
                cmd = cmd[:57] + "..."
            return f"Bash {cmd}"
        case "load_skill":
            return f"LoadSkill {args.get('name', '')}"
        case "web_fetch":
            return f"WebFetch {args.get('url', '')}"
        case "delegate":
            task = args.get("task", "")
            if len(task) > 60:
                task = task[:57] + "..."
            return f"Delegate: {task}"
        case "attach":
            name = args.get("name") or args.get("path", "")
            return f"Attach {name}"
        case "schedule":
            action = args.get("action", "")
            task_id = args.get("id", "")
            return f"Schedule {action} {task_id}".strip()
        case _:
            return f"{name} {args_str}"


async def _fetch_llamacpp_stats(api_base: str) -> dict | None:
    """Query llama.cpp /slots endpoint for KV cache and slot stats."""
    # llama.cpp serves /slots at the root, not under /v1/
    from urllib.parse import urlparse, urlunparse
    parsed = urlparse(api_base)
    url = urlunparse(parsed._replace(path="/slots"))
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            slots = resp.json()
            if not slots:
                return None
            # Aggregate across all slots
            result: dict = {"slots": []}
            for slot in slots:
                s: dict = {
                    "id": slot.get("id"),
                    "state": slot.get("state"),
                    "n_ctx": slot.get("n_ctx"),
                    "n_predict": slot.get("n_predict"),
                    "n_past": slot.get("n_past"),
                }
                # Prompt eval and generation timing from the slot
                prompt_ms = slot.get("t_prompt_processing")
                gen_ms = slot.get("t_token_generation")
                n_prompt = slot.get("n_prompt_tokens_processed")
                n_gen = slot.get("n_tokens_predicted")
                if prompt_ms and n_prompt:
                    s["prompt_tps"] = round(n_prompt / (prompt_ms / 1000), 1)
                    s["prompt_tokens_processed"] = n_prompt
                if gen_ms and n_gen:
                    s["generation_tps"] = round(n_gen / (gen_ms / 1000), 1)
                    s["tokens_predicted"] = n_gen
                result["slots"].append(s)
            return result
    except Exception as e:
        logger.debug("Failed to fetch llama.cpp stats: %s", e)
        return None


def _document_card_block(att: dict) -> dict | None:
    """Card-instead-of-content block for an ingested document, else None.

    When a staged document has a sibling card (written by
    src.document_ingest — see docs/document-ingestion.md), the conversation
    carries the ~1k-token card rather than the full text: the card's section
    map cites line numbers in the staged file, so details come from targeted
    `read` calls on the path below instead of a full inline dump.
    """
    path = att.get("path", "")
    try:
        card = Path(str(path) + ".card.md").read_text()
    except OSError:
        return None
    if not card.strip():
        return None
    return {
        "type": "text",
        "text": (
            f"[Document attachment: {att.get('filename', 'file')} — full text "
            f"staged at {path}, not inlined. The document card below maps its "
            f"contents; line references are line numbers in the staged file. "
            f"Use the read tool with offset/limit (or grep) on that path when "
            f"you need exact wording or figures.]\n{card}"
        ),
    }


def build_multimodal_content(text: str, attachments: list[dict] | None) -> str | list:
    """Build LiteLLM content from text + a staged-attachment manifest.

    Returns a plain `str` when there are no attachments (backward-compatible
    with the existing flow) or a list of content blocks otherwise.
    Images become `image_url` data-URI blocks; UTF-8 text files become
    fenced text blocks tagged with the filename.
    Anthropic rejects messages whose text blocks are empty, so when the user
    supplies no text we seed a minimal non-empty prompt referencing the files.
    """
    if not attachments:
        return text

    blocks: list[dict] = []
    # Always include a non-empty text block. Anthropic's API rejects empty
    # text blocks, and some vision models produce better responses when given
    # an explicit instruction alongside the image.
    if not text:
        names = ", ".join(att.get("filename", "file") for att in attachments)
        text = f"Please examine the attached file(s): {names}"
    blocks.append({"type": "text", "text": text})

    for att in attachments:
        mime = att["mime_type"]
        path = att["path"]
        if mime == "text/x-vision-prepass":
            with open(path, "r", encoding="utf-8") as f:
                description = f.read()
            original_mime = att.get("original_mime", "image/*")
            vision_model = att.get("vision_model", "vision sidecar model")
            blocks.append({
                "type": "text",
                "text": (
                    f"[Image attachment: {att['filename']} ({original_mime}) — "
                    f"the active model is text-only, so {vision_model} rendered "
                    f"the image into the description below. Treat this as your "
                    f"own observation of the image, not as text the user typed.]\n"
                    f"{description}"
                ),
            })
            continue
        if mime.startswith("image/"):
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            blocks.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            })
            continue
        card = _document_card_block(att)
        if card is not None:
            blocks.append(card)
        elif mime == "application/pdf":
            blocks.append({
                "type": "text",
                "text": pdf_to_text_block(att["filename"], path),
            })
        elif mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            blocks.append({
                "type": "text",
                "text": docx_to_text_block(att["filename"], path),
            })
        else:
            try:
                with open(path, "rb") as f:
                    content = f.read().decode("utf-8")
                blocks.append({
                    "type": "text",
                    "text": f"[Attachment: {att['filename']}]\n```\n{content}\n```",
                })
            except UnicodeDecodeError:
                size = att.get("size") or os.path.getsize(path)
                blocks.append({
                    "type": "text",
                    "text": (
                        f"[Attachment: {att['filename']} ({mime}, {size} bytes) "
                        f"saved at {path} — binary file, contents not inlined. "
                        f"Use the read tool if a reader is available for this format.]"
                    ),
                })

    return blocks


async def _extract_conversation(agent: Agent, session_id: str) -> None:
    """Run memory extraction for one persisted conversation.

    Reads the transcript from context/conversations/ — so a conversation no
    longer has to be live in ``agent.sessions`` to be summarized — reuses its
    recorded archive path if any, and stamps the extraction metadata back so
    the conversation is not re-extracted until it grows again.
    """
    record = conversation_store.load(agent.config.context_dir, session_id)
    if record is None:
        return
    archive_path = record.get("archive_path")
    written = await extract_learnings(
        agent.config, record["history"],
        archive_path=Path(archive_path) if archive_path else None,
    )
    conversation_store.set_extracted(
        agent.config.context_dir, session_id,
        str(written) if written is not None else archive_path,
    )


def _detect_vision_support(model: str) -> bool:
    """Ask LiteLLM whether ``model`` accepts image inputs.

    Defaults to False if the lookup raises (some provider/model combos aren't
    in LiteLLM's capability table). Safer to disable vision than to send an
    image to a model that will silently drop it.
    """
    try:
        return bool(litellm.supports_vision(model=model))
    except Exception as exc:
        logger.debug("supports_vision lookup failed for %s: %s", model, exc)
        return False


async def _vision_prepass(
    config: AgentConfig,
    text: str,
    attachments: list[dict] | None,
) -> list[dict] | None:
    """Convert image attachments to text when the main model lacks vision.

    Called before ``build_multimodal_content`` so the formatter never sees an
    image it can't render. PDFs and text attachments pass through untouched.
    Returns a new attachment list (same shape) or the original if nothing
    needed converting.

    Each replaced image becomes a synthesized ``text/plain`` attachment whose
    file contents are the description from the vision model. The formatter's
    existing text-file branch wraps it with ``[Attachment: <filename>]`` so
    the main model still sees the filename context.

    ``config.vision_model`` is required when the main model lacks vision —
    boot raises if it's unset, so this function trusts it's present here.
    """
    if config.main_model_supports_vision or not attachments:
        return attachments
    if not any(a.get("mime_type", "").startswith("image/") for a in attachments):
        return attachments

    out: list[dict] = []
    for att in attachments:
        mime = att.get("mime_type", "")
        if not mime.startswith("image/"):
            out.append(att)
            continue

        filename = att.get("filename", "image")
        logger.info(
            "Vision pre-pass: describing %s via %s",
            filename, config.vision_model,
        )
        try:
            description = await describe_image(
                config.vision_model, att["path"], mime, text,
            )
            logger.info(
                "Vision pre-pass: %s described in %d chars",
                filename, len(description),
            )
        except Exception as exc:
            logger.warning(
                "Vision pre-pass failed for %s: %s — falling back to error marker",
                filename, exc,
            )
            description = f"(vision model failed: {exc})"

        synth_path = att["path"] + ".vision.txt"
        with open(synth_path, "w", encoding="utf-8") as f:
            f.write(description)
        out.append({
            "filename": filename,
            "path": synth_path,
            "mime_type": "text/x-vision-prepass",
            "size": len(description.encode("utf-8")),
            "vision_model": config.vision_model,
            "original_mime": mime,
        })
    return out


async def agent_worker(agent: Agent, in_queue: asyncio.Queue, out_queue: asyncio.Queue):
    """Bridge between the message queues and the agent loop.

    One IncomingMessage per iteration: handle control commands inline, or
    prep its content + attachments, run the agent, and emit the reply on
    out_queue. Streaming deltas and tool-call notifications go out mid-turn
    via the on_text_delta / on_tool_call hooks.
    """
    while True:
        msg = await in_queue.get()
        logger.info("Processing message from %s (session %s)", msg.channel, msg.session_id)
        if msg.attachments:
            logger.info(
                "Inbound attachments: %s",
                [(a.get("filename"), a.get("mime_type"), a.get("size")) for a in msg.attachments],
            )

        # Slash commands arrive from channels as opaque text. Dispatch here
        # so channels stay ignorant of skills; outgoing replies go to
        # out_queue, and any synthetic prompts (e.g. /clear, /<skill>) loop
        # back through in_queue and hit the appropriate branch next iter.
        if msg.command == "slash":
            ctx = SlashContext(
                args="",
                session_id=msg.session_id,
                channel=msg.channel,
                reply_address=msg.reply_address,
                skill_dirs=agent.config.skill_dirs,
                skill_allowlist=agent.config.skill_allowlist,
            )
            result = await maybe_handle_slash(msg.content, None, ctx)
            if result is None:
                # Malformed slash frame from the client (empty text, no
                # leading slash, or bare "/"). Drop it loudly rather than
                # silently coercing it into a chat turn.
                logger.warning(
                    "Dropping malformed slash frame on session %s: %r",
                    msg.session_id, msg.content,
                )
                continue
            for out in result.outgoing:
                await out_queue.put(out)
            for inc in result.enqueue:
                await in_queue.put(inc)
            continue

        # Scratch discard: ephemeral session, no persistence and no memory
        # extraction by design. Just pop the in-memory state and ack.
        # Handled before the generic clear branch so a stray `clear` for
        # scratch (legacy clients, misrouted frames) also short-circuits here.
        if msg.command == "scratch_discard" or (
            msg.command in ("clear", "reset") and is_scratch(msg.session_id)
        ):
            agent.sessions.pop(msg.session_id, None)
            await out_queue.put(OutgoingMessage(
                content="", channel=msg.channel, session_id=msg.session_id,
                reply_address=msg.reply_address,
            ))
            continue

        # Delete (clear/reset): capture the conversation's learnings into
        # long-term memory, then remove its transcript. Extraction is awaited
        # so the memory summary lands before the transcript file is gone; the
        # summary in memory/ outlives the deleted conversation.
        if msg.command in ("clear", "reset"):
            history = agent.sessions.pop(msg.session_id, None)
            record = conversation_store.load(agent.config.context_dir, msg.session_id)
            if history is None and record is not None:
                history = record["history"]
            if history:
                archive_path = record.get("archive_path") if record else None
                await extract_learnings(
                    agent.config, list(history),
                    archive_path=Path(archive_path) if archive_path else None,
                )
            conversation_store.delete(agent.config.context_dir, msg.session_id)
            await out_queue.put(OutgoingMessage(
                content="", channel=msg.channel, session_id=msg.session_id,
                reply_address=msg.reply_address,
            ))
            continue

        if msg.command == "extract":
            # Force immediate extraction, bypassing the idle/grown gate that
            # periodic_extraction applies. Persist the live session first so
            # _extract_conversation reads an up-to-date transcript from disk.
            if msg.session_id in agent.sessions:
                conversation_store.save(
                    agent.config.context_dir, msg.session_id,
                    agent.sessions[msg.session_id],
                )
            await _extract_conversation(agent, msg.session_id)
            await out_queue.put(OutgoingMessage(
                content="", channel=msg.channel, session_id=msg.session_id,
                reply_address=msg.reply_address,
            ))
            continue

        # Streaming hooks: the agent loop fires these mid-turn so the user
        # sees tool activity and partial text without waiting for the final
        # response. Each goes out as a non-final OutgoingMessage.
        async def on_tool_call(name: str, args_str: str):
            await out_queue.put(OutgoingMessage(
                content="",
                channel=msg.channel,
                session_id=msg.session_id,
                reply_address=msg.reply_address,
                tool_calls=[_summarize_tool_call(name, args_str)],
                final=False,
            ))

        async def on_text_delta(chunk: str):
            await out_queue.put(OutgoingMessage(
                content=chunk,
                channel=msg.channel,
                session_id=msg.session_id,
                reply_address=msg.reply_address,
                delta=True,
                final=False,
            ))

        # Outbound sinks the agent fills during the turn: any files it wants
        # to attach to its reply, and a metadata bag for workflow/stats.
        attachments = []
        metadata: dict = {}

        try:
            # Inbound prep runs inside the try: route images through
            # VISION_MODEL when the main model is text-only (no-op when it
            # isn't), then format into LiteLLM content. A failed attachment
            # prep step must degrade to an error reply here — an exception
            # escaping the worker cancels the TaskGroup and takes every
            # channel down with it.
            msg_attachments = await _vision_prepass(
                agent.config, msg.content, msg.attachments,
            )
            content = build_multimodal_content(msg.content, msg_attachments)
            text = await agent.handle(
                content, msg.session_id,
                on_tool_call=on_tool_call, attachments=attachments,
                metadata=metadata,
                on_text_delta=on_text_delta,
            )
        except Exception as e:
            logger.exception("Agent error for session %s: %s", msg.session_id, e)
            text = "Sorry, I encountered an error processing your message."

        # llama.cpp exposes per-request server stats over HTTP — fold them in
        # alongside the agent's own stats so the UI can show both.
        if agent.config.api_base and metadata.get("stats"):
            llama_stats = await _fetch_llamacpp_stats(agent.config.api_base)
            if llama_stats:
                metadata["stats"]["server"] = llama_stats

        # Persist the conversation transcript after the turn so it survives
        # restarts, appears in the portal sidebar, and can be extracted from
        # disk without having to stay resident in agent.sessions.
        # Scratch sessions are deliberately in-memory only — see src/agent/scratch.py.
        if msg.session_id in agent.sessions and not is_scratch(msg.session_id):
            conversation_store.save(
                agent.config.context_dir, msg.session_id,
                agent.sessions[msg.session_id],
                channel=msg.channel,
            )

        # Final reply: routed back to the originating channel by route_outbound.
        await out_queue.put(OutgoingMessage(
            content=text,
            channel=msg.channel,
            session_id=msg.session_id,
            reply_address=msg.reply_address,
            attachments=attachments or None,
            workflow=metadata.get("workflow"),
            stats=metadata.get("stats"),
        ))


async def _run_extraction_pass(agent: Agent) -> None:
    """One disk-driven extraction pass.

    Summarizes every persisted conversation whose transcript has settled and
    grown since its last extraction (see conversation_store.due_for_extraction).
    """
    for session_id in conversation_store.due_for_extraction(agent.config.context_dir):
        await _extract_conversation(agent, session_id)


async def periodic_extraction(agent: Agent, interval_sec: int):
    """Periodically extract learnings from settled conversations on disk."""
    while True:
        await asyncio.sleep(interval_sec)
        try:
            await _run_extraction_pass(agent)
        except Exception as e:
            logger.exception("extraction pass failed: %s", e)


async def periodic_dreaming(agent: Agent, interval_sec: int):
    """Periodically run the dreaming skill to keep memory tidy.

    Sleep-first so a container restart doesn't trigger a dreaming pass.
    """
    while True:
        await asyncio.sleep(interval_sec)
        try:
            skill_content = load_skill("dreaming", agent.config.skill_dirs)
            if skill_content.startswith("Skill not found"):
                logger.warning("Dreaming skill not found; skipping")
                continue
            session_id = f"system:dreaming:{int(time.time())}"
            logger.info("Firing dreaming pass (session %s)", session_id)
            await agent.handle(
                message="",
                session_id=session_id,
                system_task_prompt=skill_content,
            )
        except Exception as e:
            logger.exception("Dreaming task failed: %s", e)


logger = logging.getLogger(__name__)


def _ensure_ws_token(path: Path) -> str:
    """Return the pairing token written at *path*, creating it if absent.

    Reusing an existing token across restarts lets long-lived CLI sessions
    survive a server bounce without having to re-read the file. Written 0600
    so other local users can't read it.
    """
    try:
        existing = path.read_text().strip()
        if existing:
            return existing
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Could not read %s (%s); regenerating", path, exc)

    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    # Write atomically with restrictive mode, then chmod to be sure on
    # filesystems where the umask leaked through.
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, token.encode("utf-8"))
    finally:
        os.close(fd)
    try:
        os.chmod(str(path), 0o600)
    except OSError:
        pass
    logger.info("WebSocket pairing token at %s", path)
    return token


_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
# Full date so the introspect skill can filter the rotating log file by a
# time window (the flat file spans multiple days; time-only stamps can't be
# windowed). `docker logs` adds its own dated prefix, so this only affects
# the file handler's lines in practice.
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"
_LOG_MAX_BYTES = 10_000_000
_LOG_BACKUP_COUNT = 3


def _configure_logging(log_file: str | None) -> None:
    """Configure root logging.

    Always logs to stderr so ``docker logs`` shows agent activity. When
    ``log_file`` is set, also attaches a rotating file handler (10 MB ×
    3 backups) at that path for the introspection skill and
    ``docker exec ... tail``.

    A ``PermissionError`` while opening the file (host-mounted volume
    owned by another UID) is downgraded to a warning — stderr still works.
    """
    level = getattr(
        logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO
    )
    logging.basicConfig(
        level=level, format=_LOG_FORMAT, datefmt=_LOG_DATEFMT
    )

    if not log_file:
        return

    try:
        os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=_LOG_MAX_BYTES,
            backupCount=_LOG_BACKUP_COUNT,
        )
    except (PermissionError, OSError) as e:
        logging.getLogger(__name__).warning(
            "LOG_FILE=%s could not be opened (%s); stderr-only.",
            log_file, e,
        )
        return

    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT))
    logging.getLogger().addHandler(handler)


async def main():
    load_dotenv()
    _configure_logging(os.environ.get("LOG_FILE", ""))
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    model = os.environ.get("MODEL")
    api_base = os.environ.get("API_BASE")
    openrouter_provider = os.environ.get("OPENROUTER_PROVIDER")
    max_history_chars = os.environ.get("MAX_HISTORY_CHARS")
    max_tool_result_chars = os.environ.get("MAX_TOOL_RESULT_CHARS")
    read_gate_bytes = os.environ.get("READ_GATE_BYTES")
    max_iterations = os.environ.get("MAX_ITERATIONS")
    attachment_dir = os.environ.get("EMAIL_ATTACHMENT_DIR")
    tts_model = os.environ.get("TTS_MODEL")
    tts_voice = os.environ.get("TTS_VOICE")
    vision_model = os.environ.get("VISION_MODEL")

    # Seed context/ from context.default/ on first run (non-overwriting).
    # Must run before building the system prompt so a fresh deployment boots
    # with the baseline identity.md instead of failing.
    bootstrap_context(Path("./context"))

    persona_name = os.environ.get("CURUNIR_PERSONA", "").strip() or DEFAULT_PERSONA
    persona = load_persona(persona_name)
    logger.info(
        "Persona '%s' active: skills=%s",
        persona.name,
        f"{len(persona.skills)} allowlisted" if persona.skills else "all on disk",
    )
    warn_missing_keys(persona, os.environ)

    config = AgentConfig(
        **({"model": model} if model else {}),
        **({"api_base": api_base} if api_base else {}),
        **({"openrouter_provider": openrouter_provider} if openrouter_provider else {}),
        **({"max_history_chars": int(max_history_chars)} if max_history_chars else {}),
        **({"max_tool_result_chars": int(max_tool_result_chars)} if max_tool_result_chars else {}),
        **({"read_gate_bytes": int(read_gate_bytes)} if read_gate_bytes is not None else {}),
        **({"max_iterations": int(max_iterations)} if max_iterations else {}),
        **({"attachment_dir": attachment_dir} if attachment_dir else {}),
        **({"tts_model": tts_model} if tts_model else {}),
        **({"tts_voice": tts_voice} if tts_voice else {}),
        **({"vision_model": vision_model} if vision_model else {}),
        persona=persona_name,
        **({"skill_allowlist": persona.skills} if persona.skills else {}),
    )
    config.main_model_supports_vision = _detect_vision_support(config.model)
    if not config.main_model_supports_vision:
        if not config.vision_model:
            raise RuntimeError(
                f"Main model {config.model} lacks vision support and no "
                "VISION_MODEL is configured. Set VISION_MODEL in the "
                "environment to a vision-capable model (e.g. "
                "openai/gpt-4o-mini) or switch MODEL to one that supports "
                "images."
            )
        logger.info(
            "Main model %s lacks vision support; routing image attachments "
            "through VISION_MODEL=%s.",
            config.model, config.vision_model,
        )

    usage_store = UsageStore(config.usage_db)

    # Initialize the SQLite schedule store (the sole schedule source of truth).
    schedule_db.init_db(str(config.schedules_db))

    agent = Agent(config, usage_store=usage_store)
    in_queue = asyncio.Queue()
    out_queue = asyncio.Queue()

    # Register channels
    channels = {}
    ws_host = os.environ.get("WS_HOST", "127.0.0.1")
    ws_port = int(os.environ.get("WS_PORT", "8765"))
    ws_token_path = Path(config.context_dir) / ".ws-token"
    ws_pairing_token = _ensure_ws_token(ws_token_path)
    ws_allowed_origins_env = os.environ.get("WS_ALLOWED_ORIGINS", "").strip()
    if ws_allowed_origins_env:
        ws_allowed_origins = frozenset(
            o.strip() for o in ws_allowed_origins_env.split(",") if o.strip()
        )
    else:
        ws_allowed_origins = None  # channel default (localhost set)
    ws = WebSocketChannel(
        in_queue, host=ws_host, port=ws_port, model=config.model,
        persona=persona.name,
        cancel_session=agent.request_cancel,
        allowed_origins=ws_allowed_origins,
        pairing_token=ws_pairing_token,
    )
    channels["cli"] = ws

    # Email channel (conditional)
    email_config = EmailChannelConfig(
        enabled=os.environ.get("EMAIL_ENABLED", "false").lower() == "true",
        imap_host=os.environ.get("FASTMAIL_IMAP_HOST", "imap.fastmail.com"),
        smtp_host=os.environ.get("FASTMAIL_SMTP_HOST", "smtp.fastmail.com"),
        user=os.environ.get("FASTMAIL_USER", ""),
        password=os.environ.get("FASTMAIL_PASSWORD", ""),
        inbox=os.environ.get("FASTMAIL_INBOX", "") or os.environ.get("FASTMAIL_USER", ""),
        poll_interval_sec=int(os.environ.get("EMAIL_POLL_INTERVAL", "60")),
        allowed_senders=[s.strip() for s in os.environ.get("EMAIL_ALLOWED_SENDERS", "").split(",") if s.strip()],
        restrict_outbound=os.environ.get("EMAIL_RESTRICT_OUTBOUND", "true").lower() == "true",
        attachment_dir=os.environ.get("EMAIL_ATTACHMENT_DIR", "/tmp/attachments"),
        state_file=Path(os.environ.get("EMAIL_STATE_FILE", "./context/email_state.json")),
        spam_score_threshold=float(os.environ.get("EMAIL_SPAM_SCORE_THRESHOLD", "5.0")),
        send_max_retries=int(os.environ.get("EMAIL_SEND_MAX_RETRIES", "5")),
        send_retry_backoff_sec=float(os.environ.get("EMAIL_SEND_RETRY_BACKOFF", "30")),
        failure_alert_threshold=int(os.environ.get("EMAIL_FAILURE_ALERT_THRESHOLD", "5")),
    )
    if email_config.enabled:
        if not email_config.user or not email_config.password:
            logger.error("EMAIL_ENABLED=true but FASTMAIL_USER or FASTMAIL_PASSWORD is unset; skipping email channel")
        else:
            email_channel = EmailChannel(in_queue, email_config)
            channels["email"] = email_channel
            logger.info("Email channel enabled for inbox %s (poll every %ds)",
                        email_config.inbox, email_config.poll_interval_sec)

    # Portal channel (conditional)
    portal_url = os.environ.get("CURUNIR_PORTAL_URL", "").strip()
    portal_token = os.environ.get("CURUNIR_PORTAL_TOKEN", "").strip()
    if portal_url and portal_token:
        portal_channel = PortalChannel(
            in_queue=in_queue,
            url=portal_url,
            token=portal_token,
            history_provider=lambda sid: agent.history_snapshot(sid),
            skills_provider=lambda: portal_skill_list(
                agent.config.skill_dirs,
                set(agent.config.skill_allowlist) if agent.config.skill_allowlist else None,
            ),
            conversations_provider=lambda: agent.conversations_snapshot(),
            cancel_session=agent.request_cancel,
        )
        channels["portal"] = portal_channel
        logger.info("Portal channel enabled for %s", portal_url)

    # Local web console (conditional). Loopback-bound, operator-only; reuses
    # the same .ws-token pairing token and Origin allowlist as the WS channel.
    local_web_config = LocalWebConfig(
        enabled=os.environ.get("LOCAL_UI_ENABLED", "false").lower() == "true",
        host=os.environ.get("LOCAL_UI_HOST", "127.0.0.1"),
        port=int(os.environ.get("LOCAL_UI_PORT", "8766")),
    )
    if local_web_config.enabled:
        local_web_channel = LocalWebChannel(
            in_queue=in_queue,
            config=config,
            host=local_web_config.host,
            port=local_web_config.port,
            model=config.model,
            persona=persona.name,
            cancel_session=agent.request_cancel,
            allowed_origins=ws_allowed_origins,
            pairing_token=ws_pairing_token,
            history_provider=lambda sid: agent.history_snapshot(sid),
            skills_provider=lambda: portal_skill_list(
                agent.config.skill_dirs,
                set(agent.config.skill_allowlist) if agent.config.skill_allowlist else None,
            ),
            # conversations_snapshot() already drops email + scratch; the extra
            # filter drops scheduled-task transcripts (interactive only).
            conversations_provider=lambda: [
                c for c in agent.conversations_snapshot()
                if not str(c.get("session_id", "")).startswith("sched:")
            ],
            # Eager document ingestion on upload (docs/document-ingestion.md):
            # one tool-less LLM pass writes <path>.card.md; the SPA blocks
            # submit until the card frame lands.
            ingest=lambda path: ingest_document(path, config, usage_store=usage_store),
            doc_card_min_bytes=int(os.environ.get("DOC_CARD_MIN_BYTES", "50000")),
        )
        channels["local_web"] = local_web_channel
        logger.info(
            "Local web console enabled on http://%s:%d/?token=%s",
            local_web_config.host, local_web_config.port, ws_pairing_token,
        )

    extraction_interval = int(os.environ.get("EXTRACTION_INTERVAL_SEC", "60"))
    dreaming_interval = int(os.environ.get("DREAMING_INTERVAL_SEC", "86400"))

    # Background loops. All three default to on, so a normal boot is
    # unchanged; an eval boot (`set -a; source .env.eval; set +a`) turns them
    # off so the instance under test does nothing but answer the harness — no
    # background LLM calls or context/memory/ writes racing the run.
    extraction_enabled = os.environ.get("MEMORY_EXTRACTION_ENABLED", "true").lower() == "true"
    dreaming_enabled = os.environ.get("DREAMING_ENABLED", "true").lower() == "true"
    scheduler_enabled = os.environ.get("SCHEDULER_ENABLED", "true").lower() == "true"
    logger.info(
        "Background loops: extraction=%s dreaming=%s scheduler=%s",
        extraction_enabled, dreaming_enabled, scheduler_enabled,
    )

    logger.info("Starting %d channel(s): %s", len(channels), ", ".join(channels.keys()))

    # Start all channels, the router, and the agent worker
    async with asyncio.TaskGroup() as tg:
        for channel in channels.values():
            tg.create_task(channel.start())
        tg.create_task(route_outbound(out_queue, channels))
        tg.create_task(agent_worker(agent, in_queue, out_queue))
        if extraction_enabled:
            tg.create_task(periodic_extraction(agent, extraction_interval))
        if dreaming_enabled:
            tg.create_task(periodic_dreaming(agent, dreaming_interval))
        if scheduler_enabled:
            tg.create_task(run_scheduler(agent))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, EOFError):
        pass
