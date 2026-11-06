# src/voice/providers.py
"""Speech providers for voice mode (#551, docs/voice-mode-plan.md §2).

Three small interfaces, chosen by ``VOICE_PROVIDER``:

- ``SpeechToText.transcribe(pcm16)``: 16-bit mono 16 kHz PCM in, text out.
- ``TextToSpeech.stream(text)``: text in, 16-bit mono 24 kHz PCM out in ~100 ms
  slices, so playback can start on the first one.
- ``TurnDetector.feed(pcm16)``: mic chunks in, ``"start"`` / ``"end"`` / ``None`` out.

``openai`` (default) speaks the OpenAI-compatible audio API, so a hosted
service or any sidecar exposing ``/v1/audio/*`` is a ``*_BASE_URL`` setting.
It has no turn detector: hosted deployments endpoint on the client.

``local`` runs everything in-process on the host: Silero VAD, Whisper
(MLX on Apple Silicon, faster-whisper on CPU/CUDA elsewhere) and Kokoro.
Its dependencies are the optional ``requirements-voice-local.txt`` and are
imported only when selected. Numbers and the traps are from the POC
(https://github.com/jalemieux/voice-poc, FINDINGS.md).
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import platform
import time
import wave
from dataclasses import dataclass
from typing import AsyncIterator, Mapping, Protocol

logger = logging.getLogger(__name__)

MIC_RATE = 16000
TTS_RATE = 24000
SLICE_BYTES = TTS_RATE * 2 // 10  # 100 ms of 16-bit mono at 24 kHz
MIN_UTTERANCE_MS = 300  # shorter captures are noise; Whisper hallucinates on them


class SpeechToText(Protocol):
    async def transcribe(self, pcm16: bytes) -> str: ...


class TextToSpeech(Protocol):
    def stream(self, text: str) -> AsyncIterator[bytes]: ...


class TurnDetector(Protocol):
    silence_ms: int

    def feed(self, pcm16: bytes) -> str | None: ...

    def reset(self) -> None: ...


# --------------------------------------------------------------------------- settings


@dataclass(frozen=True)
class VoiceSettings:
    provider: str = "openai"  # VOICE_PROVIDER: openai | local
    stt_base_url: str | None = None  # STT_BASE_URL, default the OpenAI endpoint
    stt_model: str = "gpt-4o-mini-transcribe"
    tts_base_url: str | None = None
    tts_model: str = "tts-1"
    tts_voice: str = "alloy"
    api_key: str | None = None  # OPENAI_API_KEY; a sidecar usually ignores it
    local_stt_model: str | None = None  # LOCAL_STT_MODEL, default per backend
    local_tts_voice: str = "af_heart"
    vad_silence_ms: int = 500

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "VoiceSettings":
        env = os.environ if env is None else env
        return cls(
            provider=env.get("VOICE_PROVIDER", "openai").lower(),
            stt_base_url=env.get("STT_BASE_URL") or None,
            stt_model=env.get("STT_MODEL", cls.stt_model),
            tts_base_url=env.get("TTS_BASE_URL") or None,
            tts_model=env.get("TTS_MODEL", cls.tts_model),
            tts_voice=env.get("TTS_VOICE", cls.tts_voice),
            api_key=env.get("OPENAI_API_KEY") or None,
            local_stt_model=env.get("LOCAL_STT_MODEL") or None,
            local_tts_voice=env.get("LOCAL_TTS_VOICE", cls.local_tts_voice),
            vad_silence_ms=int(env.get("VAD_SILENCE_MS", cls.vad_silence_ms)),
        )


def slices(pcm: bytes, size: int = SLICE_BYTES) -> list[bytes]:
    """Cut a PCM byte string into playable slices on a sample boundary."""
    size -= size % 2
    return [pcm[i:i + size] for i in range(0, len(pcm), size)]


def pcm16_to_wav(pcm16: bytes, rate: int = MIC_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm16)
    return buf.getvalue()


# --------------------------------------------------------------------------- openai-compatible


class OpenAISpeechToText:
    """``POST /v1/audio/transcriptions`` on any OpenAI-compatible server."""

    def __init__(self, settings: VoiceSettings):
        from openai import AsyncOpenAI

        self.model = settings.stt_model
        self._client = AsyncOpenAI(api_key=settings.api_key or "none", base_url=settings.stt_base_url)

    async def transcribe(self, pcm16: bytes) -> str:
        if len(pcm16) < MIC_RATE * 2 * MIN_UTTERANCE_MS // 1000:
            return ""
        result = await self._client.audio.transcriptions.create(
            model=self.model, file=("speech.wav", pcm16_to_wav(pcm16), "audio/wav"), language="en"
        )
        return (getattr(result, "text", None) or "").strip()


class OpenAITextToSpeech:
    """Streaming ``POST /v1/audio/speech`` with ``response_format=pcm`` (24 kHz)."""

    def __init__(self, settings: VoiceSettings):
        from openai import AsyncOpenAI

        self.model = settings.tts_model
        self.voice = settings.tts_voice
        self._client = AsyncOpenAI(api_key=settings.api_key or "none", base_url=settings.tts_base_url)

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        async with self._client.audio.speech.with_streaming_response.create(
            model=self.model, voice=self.voice, input=text, response_format="pcm"
        ) as response:
            carry = b""
            async for chunk in response.iter_bytes(SLICE_BYTES):
                data = carry + chunk
                cut = len(data) - len(data) % 2  # keep sample boundaries
                carry = data[cut:]
                if cut:
                    yield data[:cut]
            if carry:
                yield carry


# --------------------------------------------------------------------------- local


def stt_backend() -> str:
    """``mlx`` on Apple Silicon, ``faster-whisper`` (CTranslate2) everywhere else."""
    return "mlx" if platform.system() == "Darwin" and platform.machine() == "arm64" else "faster-whisper"


DEFAULT_LOCAL_STT_MODEL = {"mlx": "mlx-community/whisper-large-v3-turbo", "faster-whisper": "base"}


def pin_torch_threads() -> None:
    """silero_vad's loader calls ``torch.set_num_threads(1)`` at import; left there, Kokoro
    takes three times longer per sentence (1.6 s instead of 0.5 s on a 16-thread Ryzen).
    Call after every Silero load."""
    import torch

    torch.set_num_threads(int(os.getenv("TTS_THREADS", "0")) or max(1, (os.cpu_count() or 2) // 2))


class LocalModels:
    """The three local models, loaded once per process on first use and warmed."""

    def __init__(self, settings: VoiceSettings):
        self.backend = stt_backend()
        self.stt_model = settings.local_stt_model or DEFAULT_LOCAL_STT_MODEL[self.backend]
        self.tts_voice = settings.local_tts_voice
        self._lock = asyncio.Lock()
        self._loaded = False

    async def ready(self) -> None:
        async with self._lock:
            if not self._loaded:
                await asyncio.to_thread(self._load)
                self._loaded = True

    def _load(self) -> None:
        t = time.monotonic()
        from kokoro import KPipeline

        if self.backend == "mlx":
            import mlx_whisper

            self._whisper = mlx_whisper
        else:
            from faster_whisper import WhisperModel

            # CTranslate2 and torch each bring an OpenMP pool; Whisper on every core makes
            # Kokoro, which runs right after it, slower. Four threads is as fast as sixteen.
            threads = int(os.getenv("STT_THREADS", "0")) or min(4, os.cpu_count() or 4)
            self._whisper = WhisperModel(self.stt_model, device="auto", compute_type="int8", cpu_threads=threads)
        self._tts = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
        SileroTurnDetector()  # downloads and caches the VAD model, and pins the torch threads
        pcm = self.synthesize("Ready.")  # warm both so the first turn does not pay for it
        self.transcribe(_resample(pcm, TTS_RATE, MIC_RATE))
        logger.info("voice: local models warm in %.1fs (%s %s, kokoro %s)",
                    time.monotonic() - t, self.backend, self.stt_model, self.tts_voice)

    def transcribe(self, pcm16: bytes) -> str:
        import numpy as np

        audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        if len(audio) < MIC_RATE * MIN_UTTERANCE_MS / 1000:
            return ""
        if self.backend == "mlx":
            result = self._whisper.transcribe(audio, path_or_hf_repo=self.stt_model, language="en")
            segments = result.get("segments") or []
            if segments and all(s.get("no_speech_prob", 0) > 0.6 for s in segments):
                return ""
            return result["text"].strip()
        segments, _ = self._whisper.transcribe(audio, language="en", beam_size=1)
        return " ".join(s.text.strip() for s in segments if s.no_speech_prob <= 0.6).strip()

    def synthesize(self, text: str) -> bytes:
        import numpy as np

        parts = [np.asarray(a, dtype=np.float32) for _, _, a in self._tts(text, voice=self.tts_voice)]
        if not parts:
            return b""
        return (np.clip(np.concatenate(parts), -1.0, 1.0) * 32767).astype(np.int16).tobytes()


def _resample(pcm16: bytes, src: int, dst: int) -> bytes:
    import numpy as np

    audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32)
    idx = np.arange(0, len(audio), src / dst)
    return np.interp(idx, np.arange(len(audio)), audio).astype(np.int16).tobytes()


class LocalSpeechToText:
    def __init__(self, models: LocalModels):
        self._models = models
        self._lock = asyncio.Lock()  # one transcription at a time: the models are not thread-safe

    async def transcribe(self, pcm16: bytes) -> str:
        await self._models.ready()
        async with self._lock:
            return await asyncio.to_thread(self._models.transcribe, pcm16)


class LocalTextToSpeech:
    def __init__(self, models: LocalModels):
        self._models = models
        self._lock = asyncio.Lock()  # the speaker runs ahead on two sentences; Kokoro and MLX take one call at a time

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        await self._models.ready()
        async with self._lock:
            pcm = await asyncio.to_thread(self._models.synthesize, text)
        for piece in slices(pcm):
            yield piece


class SileroTurnDetector:
    """Silero VAD over 16 kHz PCM16 chunks of any size; robust to room noise."""

    CHUNK = 512  # samples per Silero call (32 ms)

    def __init__(self, silence_ms: int = 500):
        import numpy as np
        import torch
        from silero_vad import VADIterator, load_silero_vad

        self._np, self._torch = np, torch
        self._it = VADIterator(load_silero_vad(), threshold=0.5, sampling_rate=MIC_RATE,
                               min_silence_duration_ms=silence_ms, speech_pad_ms=200)
        pin_torch_threads()
        self._buf = np.zeros(0, dtype=np.float32)
        self.silence_ms = silence_ms

    def feed(self, pcm16: bytes) -> str | None:
        np = self._np
        self._buf = np.concatenate([self._buf, np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0])
        result = None
        while len(self._buf) >= self.CHUNK:
            chunk, self._buf = self._buf[:self.CHUNK], self._buf[self.CHUNK:]
            r = self._it(self._torch.from_numpy(chunk))
            if r and "start" in r:
                result = "start"
            elif r and "end" in r:
                result = "end"
        return result

    def reset(self) -> None:
        self._it.reset_states()
        self._buf = self._np.zeros(0, dtype=self._np.float32)


# --------------------------------------------------------------------------- factory


@dataclass
class VoiceProviders:
    stt: SpeechToText
    tts: TextToSpeech
    settings: VoiceSettings

    def turn_detector(self) -> TurnDetector | None:
        """A fresh server-side turn detector, or None when the client must endpoint."""
        if self.settings.provider != "local":
            return None
        return SileroTurnDetector(self.settings.vad_silence_ms)

    async def warm(self) -> None:
        """Load and warm the local models now (20 s to a minute on a CPU box, plus the
        first-time download), so the first utterance of a session does not pay for it."""
        models = getattr(self.tts, "_models", None)
        if models is not None:
            await models.ready()


_providers: VoiceProviders | None = None


def get_providers() -> VoiceProviders:
    """The process-wide providers, built from the environment on first use."""
    global _providers
    if _providers is None:
        _providers = make_providers()
    return _providers


def make_providers(settings: VoiceSettings | None = None) -> VoiceProviders:
    settings = settings or VoiceSettings.from_env()
    if settings.provider == "local":
        models = LocalModels(settings)
        return VoiceProviders(LocalSpeechToText(models), LocalTextToSpeech(models), settings)
    if settings.provider != "openai":
        raise ValueError(f"VOICE_PROVIDER must be 'openai' or 'local', not {settings.provider!r}")
    return VoiceProviders(OpenAISpeechToText(settings), OpenAITextToSpeech(settings), settings)
