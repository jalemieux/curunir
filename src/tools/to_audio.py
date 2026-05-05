# src/tools/to_audio.py
"""to_audio: rewrite text for speech and synthesize an MP3 attachment."""
import logging
import os
from datetime import date

from openai import AsyncOpenAI

from src.config import AgentConfig
from src.llm import call_llm

logger = logging.getLogger(__name__)


SPEECH_REWRITE_PROMPT = (
    "Rewrite the following text into a natural, spoken-word script for "
    "audio narration. Convert bullet points, lists, and headers into flowing "
    "prose with smooth transitions. Expand abbreviations and acronyms on "
    "first use. Replace emoji and markdown formatting with plain words or "
    "drop them. Keep the tone warm and conversational, like a knowledgeable "
    "friend reading the day's update aloud. Do not add any preamble, "
    "sign-off, or commentary about the rewrite — output only the spoken "
    "script itself."
)


async def exec_to_audio(
    args: dict,
    config: AgentConfig,
    attachments: list[dict] | None = None,
    on_tool_call=None,
) -> str:
    """Rewrite ``content`` for speech, synthesize an MP3, and attach it."""
    content = args.get("content")
    if not content:
        return "Error: 'content' is required"

    voice = args.get("voice") or config.tts_voice
    model = args.get("model") or config.tts_model
    filename = args.get("filename") or f"digest-{date.today().isoformat()}.mp3"

    try:
        rewrite = await call_llm(
            model=config.model,
            messages=[
                {"role": "system", "content": SPEECH_REWRITE_PROMPT},
                {"role": "user", "content": content},
            ],
            tools=[],
            api_base=config.api_base,
            openrouter_provider=config.openrouter_provider,
        )
        script = (rewrite.text or "").strip()
        if not script:
            return "Error: speech rewrite produced empty output"
    except Exception as exc:
        logger.warning("to_audio rewrite failed: %s", exc)
        return f"Error: speech rewrite failed: {exc}"

    try:
        client = AsyncOpenAI()
        response = await client.audio.speech.create(
            model=model,
            voice=voice,
            input=script,
        )
        audio_bytes = _extract_audio_bytes(response)
    except Exception as exc:
        logger.warning("to_audio TTS failed: %s", exc)
        return f"Error: text-to-speech failed: {exc}"

    out_dir = os.path.join(os.path.abspath(config.attachment_dir), "audio")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, filename)
    with open(out_path, "wb") as f:
        f.write(audio_bytes)

    size = len(audio_bytes)
    attachment = {
        "filename": filename,
        "path": out_path,
        "mime_type": "audio/mpeg",
        "size": size,
    }
    if attachments is not None:
        attachments.append(attachment)

    return f"Audio attached: {filename} ({_format_size(size)})"


def _extract_audio_bytes(response) -> bytes:
    """Pull MP3 bytes out of the OpenAI TTS response.

    Newer SDKs return an object with ``.content`` already populated; older
    streaming responses expose ``.read()``. Try both before giving up.
    """
    content = getattr(response, "content", None)
    if isinstance(content, (bytes, bytearray)):
        return bytes(content)
    read = getattr(response, "read", None)
    if callable(read):
        return read()
    raise RuntimeError("OpenAI TTS response had no readable audio bytes")


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"
