# src/voice/listener.py
"""Audio in for voice mode (#551 §4, #614): the mic stream of one session becomes
user turns.

A client in voice mode sends ``{"type": "audio", "data": <base64 PCM16 16 kHz mono>}``
frames continuously, and either lets the server end each utterance (the
``local`` provider's Silero turn detector) or ends it itself with
``{"type": "audio_end"}`` (push-to-talk release, or a client VAD). The
``VoiceListener`` buffers the audio, transcribes the utterance through the
``SpeechToText`` provider, tells the client what it heard (``transcript``
frame) and enqueues the text as a voice turn, which the speaker (speaker.py)
then answers as audio. ``{"type": "voice_on"}`` opens the listener and is
answered with the mode the server can offer; ``{"type": "voice_off"}`` drops
it. Every channel calls ``handle_voice_frame`` with its own enqueue, emit and
cancel callables, so the logic lives here once.
"""
from __future__ import annotations

import asyncio
import base64
import collections
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from src.channels.base import IncomingMessage

logger = logging.getLogger(__name__)

VOICE_FRAME_TYPES = ("voice_on", "voice_off", "audio", "audio_end")
PREROLL_CHUNKS = 12  # about 0.5 s of 40 ms chunks kept before the detector says "start"

Emit = Callable[[dict], Awaitable[None]]
Enqueue = Callable[[IncomingMessage], Awaitable[None]]
Cancel = Callable[[], object]


def _now_ms() -> float:
    return time.time() * 1000.0


class VoiceListener:
    def __init__(self, providers, *, channel: str, session_id: str, agent: str | None,
                 enqueue: Enqueue, emit: Emit, cancel: Cancel):
        self.stt = providers.stt
        self.detector = providers.turn_detector()  # None: the client ends utterances
        self.channel, self.session_id, self.agent = channel, session_id, agent
        self.enqueue, self.emit, self.cancel = enqueue, emit, cancel
        self.preroll: collections.deque[bytes] = collections.deque(maxlen=PREROLL_CHUNKS)
        self.utterance: list[bytes] | None = None
        self._pending: asyncio.Task | None = None  # the utterance being transcribed; chained in order

    @property
    def server_vad(self) -> bool:
        return self.detector is not None

    def _frame(self, frame: dict) -> dict:
        return {**frame, "session_id": self.session_id, **({"agent": self.agent} if self.agent else {})}

    async def ready(self) -> None:
        await self.emit(self._frame({"type": "voice_state", "state": "ready", "server_vad": self.server_vad}))

    async def audio(self, pcm16: bytes) -> None:
        if self.detector is None:
            if self.utterance is None:
                self.utterance = []
            self.utterance.append(pcm16)
            return
        self.preroll.append(pcm16)
        if self.utterance is not None:
            self.utterance.append(pcm16)
        signal = self.detector.feed(pcm16)
        if signal == "start":
            await self._speech_start()
        elif signal == "end" and self.utterance is not None:
            await self._speech_end(silence_ms=self.detector.silence_ms)

    async def audio_end(self) -> None:
        """The client ended the utterance (push-to-talk release, client VAD)."""
        if self.utterance is not None:
            await self._speech_end(silence_ms=0)

    async def _speech_start(self) -> None:
        self.utterance = list(self.preroll)
        # Barge-in is the client's call: on this "listening" it clears its playback and
        # sends the interrupt with the last played seq, the one number the server cannot
        # know (speaker.py). One path, one source of truth for what was heard.
        await self.emit(self._frame({"type": "voice_state", "state": "listening"}))

    async def _speech_end(self, *, silence_ms: int) -> None:
        """Hand the utterance to a task, chained after the previous one so turns keep
        their order, and return: the channel's receive loop must not wait on STT."""
        pcm = b"".join(self.utterance or [])
        self.utterance = None
        stamps = {"speech_end": _now_ms() - silence_ms}
        await self.emit(self._frame({"type": "voice_state", "state": "transcribing"}))
        previous = self._pending
        self._pending = asyncio.create_task(self._transcribe(pcm, silence_ms, stamps, previous))

    async def _transcribe(self, pcm: bytes, silence_ms: int, stamps: dict, previous: asyncio.Task | None) -> None:
        if previous is not None:
            await asyncio.gather(previous, return_exceptions=True)
        t_start = _now_ms()
        try:
            text = await self.stt.transcribe(pcm)
        except Exception:
            logger.exception("voice: transcription failed on %s session %s", self.channel, self.session_id)
            text = ""
        stamps["transcript"] = _now_ms()
        stt_ms = round(stamps["transcript"] - t_start)
        await self.emit(self._frame({
            "type": "transcript", "text": text, "stt_ms": stt_ms, "vad_silence_ms": silence_ms,
            "audio_ms": len(pcm) // 32,
        }))
        logger.info("voice: %s session %s heard %r (%d ms audio, stt %d ms)",
                    self.channel, self.session_id, text, len(pcm) // 32, stt_ms)
        if text:
            await self.enqueue(IncomingMessage(
                content=text, channel=self.channel, session_id=self.session_id, reply_address={},
                agent=self.agent, voice=True, voice_stamps=stamps,
            ))
        else:
            await self.emit(self._frame({"type": "voice_state", "state": "ready", "server_vad": self.server_vad}))

    async def flush(self) -> None:
        """Wait for the transcription in flight (tests, shutdown)."""
        if self._pending is not None:
            await asyncio.gather(self._pending, return_exceptions=True)

    def close(self) -> None:
        self.utterance = None
        if self.detector is not None:
            self.detector.reset()
        if self._pending is not None and not self._pending.done():
            self._pending.cancel()


@dataclass
class Listeners:
    """The live listeners of a process, one per (channel, session, agent)."""

    _by_key: dict[tuple[str, str, str | None], VoiceListener] = field(default_factory=dict)
    _told_off: set[tuple[str, str]] = field(default_factory=set)  # (channel, session) told "off" once

    def get(self, channel: str, session_id: str, agent: str | None) -> VoiceListener | None:
        return self._by_key.get((channel, session_id, agent))

    def open(self, listener: VoiceListener) -> VoiceListener:
        key = (listener.channel, listener.session_id, listener.agent)
        old = self._by_key.pop(key, None)
        if old is not None:
            old.close()
        self._by_key[key] = listener
        return listener

    def close(self, channel: str, session_id: str, agent: str | None) -> None:
        old = self._by_key.pop((channel, session_id, agent), None)
        if old is not None:
            old.close()

    def close_session(self, channel: str, session_id: str) -> None:
        for key in [k for k in self._by_key if k[0] == channel and k[1] == session_id]:
            self._by_key.pop(key).close()
        self._told_off.discard((channel, session_id))

    def close_channel(self, channel: str) -> None:
        for key in [k for k in self._by_key if k[0] == channel]:
            self._by_key.pop(key).close()
        self._told_off = {k for k in self._told_off if k[0] != channel}

    def tell_off_once(self, channel: str, session_id: str) -> bool:
        """True the first time audio arrives with no listener for the session."""
        if (channel, session_id) in self._told_off:
            return False
        self._told_off.add((channel, session_id))
        return True


listeners = Listeners()


async def handle_voice_frame(payload: dict, *, channel: str, session_id: str, agent: str | None,
                             enqueue: Enqueue, emit: Emit, cancel: Cancel, providers=None) -> bool:
    """Handle one voice frame from a client. False when the payload is not one."""
    kind = payload.get("type")
    if kind not in VOICE_FRAME_TYPES:
        return False
    if kind == "voice_on":
        if providers is None:
            from src.voice.providers import get_providers

            try:
                providers = get_providers()
            except Exception:
                logger.exception("voice: speech providers unavailable")
                await emit({"type": "voice_state", "state": "unavailable", "session_id": session_id})
                return True
        listener = listeners.open(VoiceListener(
            providers, channel=channel, session_id=session_id, agent=agent,
            enqueue=enqueue, emit=emit, cancel=cancel,
        ))
        listeners._told_off.discard((channel, session_id))
        await listener.ready()
        return True
    if kind == "voice_off":
        listeners.close(channel, session_id, agent)
        return True
    listener = listeners.get(channel, session_id, agent)
    if listener is None:
        # Audio after a restart or a voice_off: say so once, not 25 times a second.
        if listeners.tell_off_once(channel, session_id):
            await emit({"type": "voice_state", "state": "off", "session_id": session_id})
        return True
    if kind == "audio":
        try:
            pcm = base64.b64decode(payload.get("data") or "")
        except (ValueError, TypeError):
            return True
        if pcm:
            await listener.audio(pcm)
    else:
        await listener.audio_end()
    return True
