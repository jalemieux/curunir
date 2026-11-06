# tests/test_voice_providers.py
import wave
import io
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.voice import providers
from src.voice.providers import (
    MIC_RATE,
    SLICE_BYTES,
    LocalModels,
    LocalTextToSpeech,
    LocalSpeechToText,
    OpenAISpeechToText,
    OpenAITextToSpeech,
    VoiceSettings,
    make_providers,
    pcm16_to_wav,
    slices,
)


def test_settings_from_env_defaults_and_overrides():
    s = VoiceSettings.from_env({})
    assert s.provider == "openai" and s.stt_base_url is None and s.tts_model == "tts-1"
    s = VoiceSettings.from_env({
        "VOICE_PROVIDER": "LOCAL", "STT_BASE_URL": "http://sidecar:8000/v1", "TTS_MODEL": "gpt-4o-mini-tts",
        "TTS_VOICE": "nova", "OPENAI_API_KEY": "k", "LOCAL_STT_MODEL": "small", "VAD_SILENCE_MS": "700",
    })
    assert s.provider == "local" and s.stt_base_url == "http://sidecar:8000/v1"
    assert s.tts_model == "gpt-4o-mini-tts" and s.tts_voice == "nova" and s.api_key == "k"
    assert s.local_stt_model == "small" and s.vad_silence_ms == 700


def test_slices_keep_sample_boundaries():
    pcm = bytes(range(256)) * 40  # 10240 bytes
    parts = slices(pcm, 4800)
    assert [len(p) for p in parts] == [4800, 4800, 640]
    assert b"".join(parts) == pcm
    assert all(len(p) % 2 == 0 for p in slices(pcm, 4801))


def test_pcm16_to_wav_header():
    wav = pcm16_to_wav(b"\x00\x01" * 1600)
    with wave.open(io.BytesIO(wav)) as wf:
        assert (wf.getnchannels(), wf.getsampwidth(), wf.getframerate(), wf.getnframes()) == (1, 2, MIC_RATE, 1600)


def _openai_client(speech_chunks=(), transcript="hello"):
    client = MagicMock()
    response = MagicMock()

    async def iter_bytes(size):
        for c in speech_chunks:
            yield c

    response.iter_bytes = iter_bytes

    @asynccontextmanager
    async def create(**kwargs):
        client.speech_kwargs = kwargs
        yield response

    client.audio.speech.with_streaming_response.create = create
    client.audio.transcriptions.create = AsyncMock(return_value=MagicMock(text=transcript))
    return client


async def test_openai_tts_streams_pcm_slices_on_sample_boundaries():
    client = _openai_client(speech_chunks=[b"\x01" * 4801, b"\x02" * 3])
    with patch("openai.AsyncOpenAI", return_value=client):
        tts = OpenAITextToSpeech(VoiceSettings(tts_model="tts-1", tts_voice="nova", tts_base_url="http://x/v1"))
    out = [c async for c in tts.stream("Hello there.")]
    assert b"".join(out) == b"\x01" * 4801 + b"\x02" * 3
    assert all(len(c) % 2 == 0 for c in out[:-1])
    assert client.speech_kwargs == {"model": "tts-1", "voice": "nova", "input": "Hello there.", "response_format": "pcm"}


async def test_openai_stt_sends_wav_and_skips_short_audio():
    client = _openai_client(transcript=" What time is it? ")
    with patch("openai.AsyncOpenAI", return_value=client):
        stt = OpenAISpeechToText(VoiceSettings(stt_model="whisper-1"))
    assert await stt.transcribe(b"\x00" * 100) == ""
    client.audio.transcriptions.create.assert_not_awaited()
    assert await stt.transcribe(b"\x00\x01" * MIC_RATE) == "What time is it?"
    kwargs = client.audio.transcriptions.create.await_args.kwargs
    assert kwargs["model"] == "whisper-1"
    name, data, mime = kwargs["file"]
    assert name.endswith(".wav") and mime == "audio/wav" and data[:4] == b"RIFF"


def test_local_defaults_follow_platform(monkeypatch):
    monkeypatch.setattr(providers.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(providers.platform, "machine", lambda: "arm64")
    assert LocalModels(VoiceSettings()).stt_model == "mlx-community/whisper-large-v3-turbo"
    monkeypatch.setattr(providers.platform, "system", lambda: "Linux")
    m = LocalModels(VoiceSettings())
    assert m.backend == "faster-whisper" and m.stt_model == "base"
    assert LocalModels(VoiceSettings(local_stt_model="small")).stt_model == "small"


class _Seg:
    def __init__(self, text, p):
        self.text, self.no_speech_prob = text, p


class _FakeWhisper:
    def transcribe(self, audio, language, beam_size):
        return [_Seg(" Hello ", 0.1), _Seg("noise", 0.9), _Seg("there.", 0.2)], None


def _fake_kokoro(text, voice):
    import numpy as np

    yield ("g", "p", np.full(24000, 0.5, dtype=np.float32))  # one second


def _warm_models(monkeypatch):
    monkeypatch.setattr(providers.platform, "system", lambda: "Linux")
    m = LocalModels(VoiceSettings())
    m._whisper, m._tts, m._loaded = _FakeWhisper(), _fake_kokoro, True
    return m


async def test_local_stt_joins_segments_and_drops_no_speech(monkeypatch):
    pytest.importorskip("numpy")
    stt = LocalSpeechToText(_warm_models(monkeypatch))
    assert await stt.transcribe(b"\x00\x01" * MIC_RATE) == "Hello there."
    assert await stt.transcribe(b"\x00" * 100) == ""


async def test_local_tts_streams_100ms_slices(monkeypatch):
    pytest.importorskip("numpy")
    tts = LocalTextToSpeech(_warm_models(monkeypatch))
    out = [c async for c in tts.stream("Hello.")]
    assert len(out) == 10 and all(len(c) == SLICE_BYTES for c in out)


async def test_warm_loads_the_local_models_only(monkeypatch):
    p = make_providers(VoiceSettings(provider="local"))
    calls = []

    async def ready():
        calls.append(1)

    monkeypatch.setattr(p.tts._models, "ready", ready)
    await p.warm()
    assert calls == [1]
    with patch("openai.AsyncOpenAI"):
        await make_providers(VoiceSettings(provider="openai")).warm()  # nothing to load


def test_make_providers_selects_by_setting():
    with patch("openai.AsyncOpenAI"):
        p = make_providers(VoiceSettings(provider="openai"))
    assert isinstance(p.stt, OpenAISpeechToText) and p.turn_detector() is None
    p = make_providers(VoiceSettings(provider="local"))
    assert isinstance(p.tts, LocalTextToSpeech)
    with pytest.raises(ValueError):
        make_providers(VoiceSettings(provider="gemini"))


async def test_local_tts_calls_are_serialized(monkeypatch):
    """The speaker runs two sentences ahead; the local models take one call at a time."""
    pytest.importorskip("numpy")
    import asyncio
    import time

    m = _warm_models(monkeypatch)
    active = {"n": 0, "max": 0}

    def slow_kokoro(text, voice):
        import numpy as np

        active["n"] += 1
        active["max"] = max(active["max"], active["n"])
        time.sleep(0.02)
        active["n"] -= 1
        yield ("g", "p", np.zeros(2400, dtype=np.float32))

    m._tts = slow_kokoro
    tts = LocalTextToSpeech(m)

    async def one(text):
        return [c async for c in tts.stream(text)]

    await asyncio.gather(one("a"), one("b"), one("c"))
    assert active["max"] == 1
