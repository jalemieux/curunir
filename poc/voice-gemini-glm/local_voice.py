"""Local speech primitives for the "local" mode: Whisper (MLX) in, Kokoro out, and a
streaming sentence splitter. No network, no server imports; heavy libraries load lazily."""
from __future__ import annotations

import asyncio
import logging
import re
import time

import numpy as np

log = logging.getLogger("voice-poc.local")

MIC_RATE = 16000
TTS_RATE = 24000  # Kokoro's native rate, and what the browser's player expects
MIN_UTTERANCE_MS = 300  # shorter captures are noise, Whisper would hallucinate on them
VAD_CHUNK = 512  # samples per Silero call at 16 kHz (32 ms)
VAD_SILENCE_MS = 500  # silence that ends an utterance

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")


class SentenceSplitter:
    """Feed token deltas, get back complete sentences as soon as they end."""

    def __init__(self, min_chars: int = 8):
        self.buf = ""
        self.min_chars = min_chars

    def feed(self, delta: str) -> list[str]:
        self.buf += delta
        out: list[str] = []
        start = 0
        for m in _SENTENCE_END.finditer(self.buf):
            candidate = self.buf[start:m.start()].strip()
            if len(candidate) >= self.min_chars:  # skip "Dr." style fragments
                out.append(candidate)
                start = m.end()
        self.buf = self.buf[start:]
        return out

    def flush(self) -> str | None:
        rest = self.buf.strip()
        self.buf = ""
        return rest or None


def resample(audio: np.ndarray, src: int, dst: int) -> np.ndarray:
    idx = np.arange(0, len(audio), src / dst)
    return np.interp(idx, np.arange(len(audio)), audio).astype(np.float32)


class TurnDetector:
    """Silero VAD over a stream of 16 kHz PCM16 chunks of any size. feed() returns
    "start", "end" or None. Robust to room noise, unlike an energy threshold."""

    def __init__(self, silence_ms: int = VAD_SILENCE_MS):
        import torch
        from silero_vad import VADIterator, load_silero_vad

        self._torch = torch
        self._it = VADIterator(load_silero_vad(), threshold=0.5, sampling_rate=MIC_RATE,
                               min_silence_duration_ms=silence_ms, speech_pad_ms=200)
        self._buf = np.zeros(0, dtype=np.float32)
        self.silence_ms = silence_ms

    def feed(self, pcm16: bytes) -> str | None:
        self._buf = np.concatenate([self._buf, np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0])
        result = None
        while len(self._buf) >= VAD_CHUNK:
            chunk, self._buf = self._buf[:VAD_CHUNK], self._buf[VAD_CHUNK:]
            r = self._it(self._torch.from_numpy(chunk))
            if r and "start" in r:
                result = "start"
            elif r and "end" in r:
                result = "end"
        return result

    def reset(self) -> None:
        self._it.reset_states()
        self._buf = np.zeros(0, dtype=np.float32)


class LocalSpeech:
    """Whisper for speech-to-text and Kokoro for text-to-speech, loaded on first use."""

    def __init__(self, stt_model: str, tts_voice: str):
        self.stt_model = stt_model
        self.tts_voice = tts_voice
        self._lock = asyncio.Lock()
        self._loaded = False

    async def ready(self) -> None:
        async with self._lock:
            if not self._loaded:
                await asyncio.to_thread(self._load)
                self._loaded = True

    def _load(self) -> None:
        t = time.monotonic()
        import mlx_whisper
        from kokoro import KPipeline

        self._whisper = mlx_whisper
        self._tts = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
        TurnDetector()  # download and cache the VAD model too
        # Warm both models so the first real turn does not pay for loading and compiling.
        pcm = self.synthesize("Ready.")
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        self.transcribe(resample(audio, TTS_RATE, MIC_RATE))
        log.info("local models loaded and warm in %.1fs (%s, kokoro %s)", time.monotonic() - t, self.stt_model, self.tts_voice)

    def turn_detector(self) -> TurnDetector:
        return TurnDetector()

    def transcribe(self, pcm16: bytes | np.ndarray) -> str:
        """16 kHz mono PCM16 bytes (or float32 array) -> text, "" when nothing usable was said."""
        audio = pcm16 if isinstance(pcm16, np.ndarray) else np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        if len(audio) < MIC_RATE * MIN_UTTERANCE_MS / 1000:
            return ""
        result = self._whisper.transcribe(audio, path_or_hf_repo=self.stt_model, language="en")
        segments = result.get("segments") or []
        if segments and all(s.get("no_speech_prob", 0) > 0.6 for s in segments):
            return ""
        return result["text"].strip()

    def synthesize(self, text: str) -> bytes:
        """Text -> 24 kHz mono PCM16 bytes."""
        parts = [np.asarray(audio, dtype=np.float32) for _, _, audio in self._tts(text, voice=self.tts_voice)]
        if not parts:
            return b""
        audio = np.concatenate(parts)
        return (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
