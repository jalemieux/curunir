from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import AgentConfig
from src.llm import LLMResponse
from src.tools.to_audio import SPEECH_REWRITE_PROMPT, exec_to_audio


def _mock_tts_response(audio_bytes: bytes):
    """Build a mock TTS response with a .content attribute."""
    resp = MagicMock()
    resp.content = audio_bytes
    return resp


def _patch_openai(audio_bytes: bytes):
    """Patch AsyncOpenAI so .audio.speech.create returns audio bytes.

    Returns the create AsyncMock so tests can assert call args.
    """
    create_mock = AsyncMock(return_value=_mock_tts_response(audio_bytes))
    client = MagicMock()
    client.audio.speech.create = create_mock
    return patch("src.tools.to_audio.AsyncOpenAI", return_value=client), create_mock


@pytest.fixture
def tts_config(tmp_path):
    return AgentConfig(
        identity_file=tmp_path / "identity.md",
        context_dir=tmp_path,
        attachment_dir=str(tmp_path / "attachments"),
    )


class TestToAudio:
    async def test_rewrites_and_writes_mp3(self, tts_config):
        attachments: list[dict] = []
        rewrite = LLMResponse(text="Spoken script.", tool_calls=None)

        patcher, create_mock = _patch_openai(b"FAKE_MP3_BYTES")
        with patch(
            "src.tools.to_audio.call_llm", new_callable=AsyncMock, return_value=rewrite
        ) as mock_llm, patcher:
            result = await exec_to_audio(
                {"content": "- bullet\n- list", "filename": "test.mp3"},
                tts_config,
                attachments=attachments,
            )

        assert mock_llm.await_count == 1
        rewrite_messages = mock_llm.await_args.kwargs["messages"]
        # System message must be exactly the rewrite prompt; user message the
        # raw content. Locks the wiring so prompt edits stay safe.
        assert rewrite_messages[0] == {
            "role": "system",
            "content": SPEECH_REWRITE_PROMPT,
        }
        assert rewrite_messages[1] == {
            "role": "user",
            "content": "- bullet\n- list",
        }

        create_mock.assert_awaited_once()
        kwargs = create_mock.await_args.kwargs
        assert kwargs["input"] == "Spoken script."
        assert kwargs["model"] == "tts-1"
        assert kwargs["voice"] == "alloy"

        out_path = Path(tts_config.attachment_dir) / "audio" / "test.mp3"
        assert out_path.exists()
        assert out_path.read_bytes() == b"FAKE_MP3_BYTES"

        assert len(attachments) == 1
        attachment = attachments[0]
        assert attachment["filename"] == "test.mp3"
        assert attachment["path"] == str(out_path)
        assert attachment["mime_type"] == "audio/mpeg"
        assert attachment["size"] == len(b"FAKE_MP3_BYTES")

        assert "test.mp3" in result

    async def test_handles_tts_failure_gracefully(self, tts_config):
        attachments: list[dict] = []
        rewrite = LLMResponse(text="Spoken script.", tool_calls=None)

        create_mock = AsyncMock(side_effect=RuntimeError("openai down"))
        client = MagicMock()
        client.audio.speech.create = create_mock

        with patch(
            "src.tools.to_audio.call_llm", new_callable=AsyncMock, return_value=rewrite
        ), patch("src.tools.to_audio.AsyncOpenAI", return_value=client):
            result = await exec_to_audio(
                {"content": "anything"}, tts_config, attachments=attachments
            )

        assert "error" in result.lower()
        assert attachments == []

    async def test_uses_config_voice_and_model(self, tts_config):
        tts_config.tts_model = "tts-1-hd"
        tts_config.tts_voice = "nova"
        rewrite = LLMResponse(text="Hi.", tool_calls=None)

        patcher, create_mock = _patch_openai(b"abc")
        with patch(
            "src.tools.to_audio.call_llm", new_callable=AsyncMock, return_value=rewrite
        ), patcher:
            await exec_to_audio(
                {"content": "x", "filename": "v.mp3"}, tts_config, attachments=[]
            )

        kwargs = create_mock.await_args.kwargs
        assert kwargs["model"] == "tts-1-hd"
        assert kwargs["voice"] == "nova"

    async def test_arg_overrides_config(self, tts_config):
        rewrite = LLMResponse(text="Hi.", tool_calls=None)
        patcher, create_mock = _patch_openai(b"abc")
        with patch(
            "src.tools.to_audio.call_llm", new_callable=AsyncMock, return_value=rewrite
        ), patcher:
            await exec_to_audio(
                {
                    "content": "x",
                    "filename": "v.mp3",
                    "voice": "shimmer",
                    "model": "tts-1-hd",
                },
                tts_config,
                attachments=[],
            )

        kwargs = create_mock.await_args.kwargs
        assert kwargs["voice"] == "shimmer"
        assert kwargs["model"] == "tts-1-hd"

    async def test_filename_default_uses_today(self, tts_config):
        rewrite = LLMResponse(text="Hi.", tool_calls=None)
        patcher, _ = _patch_openai(b"abc")
        with patch(
            "src.tools.to_audio.call_llm", new_callable=AsyncMock, return_value=rewrite
        ), patcher:
            await exec_to_audio(
                {"content": "x"}, tts_config, attachments=[]
            )

        today = date.today().isoformat()
        expected = Path(tts_config.attachment_dir) / "audio" / f"digest-{today}.mp3"
        assert expected.exists()

    async def test_missing_content_returns_error(self, tts_config):
        attachments: list[dict] = []
        result = await exec_to_audio({}, tts_config, attachments=attachments)
        assert "error" in result.lower()
        assert attachments == []

    async def test_rewrite_failure_returns_error(self, tts_config):
        attachments: list[dict] = []
        with patch(
            "src.tools.to_audio.call_llm",
            new_callable=AsyncMock,
            side_effect=RuntimeError("llm down"),
        ):
            result = await exec_to_audio(
                {"content": "x"}, tts_config, attachments=attachments
            )
        assert "error" in result.lower()
        assert attachments == []


class TestSpeechRewritePrompt:
    """The rewrite prompt must teach TTS-friendly narration (issue #290)."""

    def test_keeps_output_constraints(self):
        prompt = SPEECH_REWRITE_PROMPT.lower()
        # No preamble/sign-off, drop markdown/emoji, output only the script.
        assert "preamble" in prompt
        assert "emoji" in prompt
        assert "markdown" in prompt

    def test_drives_pacing_through_punctuation(self):
        prompt = SPEECH_REWRITE_PROMPT.lower()
        # Pacing comes from punctuation/whitespace, not vague "flowing prose".
        assert "sentence" in prompt
        assert "pause" in prompt or "pauses" in prompt

    def test_handles_technical_identifiers(self):
        prompt = SPEECH_REWRITE_PROMPT.lower()
        # Spell/gloss code-like identifiers and expand acronyms.
        assert "acronym" in prompt
        assert "identifier" in prompt or "spell" in prompt

    def test_requires_verbal_signposting(self):
        prompt = SPEECH_REWRITE_PROMPT.lower()
        assert "signpost" in prompt or "transition" in prompt

    def test_forbids_ssml_markup(self):
        prompt = SPEECH_REWRITE_PROMPT.lower()
        # OpenAI tts-1/tts-1-hd read SSML literally — the prompt must forbid it.
        assert "ssml" in prompt
