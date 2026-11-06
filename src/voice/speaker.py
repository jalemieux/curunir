# src/voice/speaker.py
"""The speaker of a voice turn (#551, docs/voice-mode-plan.md §3 and §4).

``VoiceSpeaker`` runs as its own task, started by ``agent_worker`` for a turn
whose ``IncomingMessage.voice`` is set. The worker feeds it the text deltas it
already streams; the speaker cuts them into sentences, synthesizes each one
through the ``TextToSpeech`` provider with bounded look-ahead, and emits the
audio strictly in order as ``audio_chunk`` frames (``OutgoingMessage.audio``).
It outlives ``Agent.handle``: the text ``final`` frame goes out as today and
the audio ends later with its own ``final=true`` frame, so the worker never
waits on audio and other sessions are not blocked behind one reply's voice.

Barge-in: the model writes faster than a voice reads, so the turn has usually
finished when the user interrupts and ``Agent.request_cancel`` is a no-op. The
``SpeakerRegistry`` gives every session its own stop signal, reached through
``Container.request_cancel``; a stop drops the queued sentences, cancels the
in-flight synthesis, sends the closing frame, and records what the user heard
(from the client's last played ``seq``) so ``agent_worker`` rewrites the
persisted reply before the session's next message.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator, Awaitable, Callable, Protocol

from src.voice.text import SentenceChunker, speakable

logger = logging.getLogger(__name__)

AUDIO_MIME = "audio/pcm;rate=24000"
INTERRUPTED_MARKER = "… (interrupted by user)"
UNHEARD_MARKER = "(interrupted by user before the reply was heard)"


class TextToSpeech(Protocol):
    def stream(self, text: str) -> AsyncIterator[bytes]: ...


Emit = Callable[[dict], Awaitable[None]]  # sends one audio_chunk frame to the client


@dataclass
class _Sentence:
    index: int
    text: str
    audio: asyncio.Task | None = None  # synthesis running ahead; result is list[bytes]


class VoiceSpeaker:
    def __init__(self, tts: TextToSpeech, emit: Emit, *, session_id: str, agent: str | None = None,
                 lookahead: int = 2, turn_id: str | None = None, stamps: dict | None = None):
        self.tts = tts
        self.stamps = stamps if stamps is not None else {}  # server-clock epoch ms per stage
        self.emit = emit
        self.session_id = session_id
        self.agent = agent
        self.lookahead = max(1, lookahead)
        self.turn_id = turn_id or uuid.uuid4().hex[:8]
        self.chunker = SentenceChunker()
        self.queue: asyncio.Queue[_Sentence | None] = asyncio.Queue()
        self.fed = ""  # every delta fed, to tell unstreamed final text from streamed
        self.sentences: list[str] = []  # in speaking order
        self.last_seq_of_sentence: dict[int, int] = {}  # sentence index -> seq of its last slice
        self.seq = 0
        self.stopped = False
        self.finished = False
        self.heard: str | None = None  # set by stop(): the prefix the user heard
        self._task: asyncio.Task | None = None
        self._inflight: list[asyncio.Task] = []

    # ---- producer side (called by agent_worker)

    def feed(self, delta: str) -> None:
        """A text delta from the model, in order."""
        if self.stopped or self.finished:
            return
        self.fed += delta
        for sentence in self.chunker.feed(delta):
            self._enqueue(sentence)

    def finish(self, final_text: str | None = None) -> None:
        """The turn is over: speak what was not streamed, then close the audio."""
        if self.finished or self.stopped:
            return
        self.finished = True
        tail = self.chunker.flush()
        if tail:
            self._enqueue(tail)
        unstreamed = (final_text or "").strip()
        if unstreamed and unstreamed != "(interrupted)" and unstreamed not in self.fed:
            # Errors and other replies that never passed through on_text_delta
            # (docs/voice-mode-plan.md §3): never dead air.
            chunker = SentenceChunker()
            sentences = chunker.feed(unstreamed)
            tail = chunker.flush()
            for sentence in sentences + ([tail] if tail else []):
                self._enqueue(sentence)
        self.queue.put_nowait(None)

    def _enqueue(self, sentence: str) -> None:
        spoken = speakable(sentence).strip()
        if not spoken:
            return
        self.sentences.append(spoken)
        self.queue.put_nowait(_Sentence(len(self.sentences) - 1, spoken))

    # ---- stop (barge-in), from any task

    def stop(self, played_seq: int | None = None) -> None:
        """Drop what is queued, cancel the in-flight synthesis, close the audio."""
        if self.stopped:
            return
        self.stopped = True
        self.heard = self.heard_prefix(played_seq)
        while not self.queue.empty():
            self.queue.get_nowait()
        for t in self._inflight:
            t.cancel()
        self.queue.put_nowait(None)

    def heard_prefix(self, played_seq: int | None) -> str:
        """The sentences the user heard in full, given the client's last played ``seq``.

        Unknown ``played_seq``: everything emitted so far counts as heard."""
        heard = []
        for i, text in enumerate(self.sentences):
            last = self.last_seq_of_sentence.get(i)
            if last is None:
                break
            if played_seq is not None and last > played_seq:
                break
            heard.append(text)
        return " ".join(heard)

    # ---- the task

    def start(self) -> asyncio.Task:
        self._task = asyncio.create_task(self.run(), name=f"voice-speaker:{self.session_id}")
        return self._task

    async def run(self) -> None:
        pending: list[_Sentence] = []  # synthesizing ahead, in order
        try:
            while True:
                while len(pending) < self.lookahead:
                    if pending:
                        # Run ahead when more text is already here, but never hold a
                        # synthesized sentence back waiting for the next one to arrive.
                        try:
                            item = self.queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    else:
                        item = await self.queue.get()
                    if item is None:
                        if not pending:
                            return
                        # The turn is over; drain what is synthesizing, nothing more comes.
                        self.queue.put_nowait(None)
                        break
                    item.audio = asyncio.create_task(self._synthesize(item.text))
                    self._inflight.append(item.audio)
                    pending.append(item)
                item = pending.pop(0)
                try:
                    pieces = await item.audio
                except asyncio.CancelledError:
                    if self.stopped:
                        return
                    raise
                except Exception:
                    logger.exception("voice: synthesis failed for sentence %d of %s", item.index, self.turn_id)
                    pieces = []
                finally:
                    if item.audio in self._inflight:
                        self._inflight.remove(item.audio)
                for n, data in enumerate(pieces):
                    if self.stopped:
                        return
                    await self.emit(self._frame(item, data, first=n == 0))
                self.last_seq_of_sentence[item.index] = self.seq
        finally:
            for t in self._inflight:
                t.cancel()
            try:
                await self.emit(self._closing_frame())
            except Exception:
                logger.warning("voice: closing frame not delivered for %s", self.turn_id)

    async def _synthesize(self, text: str) -> list[bytes]:
        return [piece async for piece in self.tts.stream(text)]

    def _frame(self, item: _Sentence, data: bytes, *, first: bool) -> dict:
        self.seq += 1
        if self.seq == 1:
            self.stamps["first_audio_chunk_sent"] = time.time() * 1000.0
            stages = self.stages()
            if stages:
                logger.info("voice: session %s first audio %s", self.session_id,
                            ", ".join(f"{k} {v} ms" for k, v in stages.items()))
        frame = {
            "type": "audio_chunk", "turn_id": self.turn_id, "seq": self.seq, "sentence": item.index,
            "mime": AUDIO_MIME, "data": base64.b64encode(data).decode("ascii"), "final": False,
        }
        if first:
            frame["text"] = item.text
        return frame

    def _closing_frame(self) -> dict:
        self.seq += 1
        self.stamps["audio_final"] = time.time() * 1000.0
        return {"type": "audio_chunk", "turn_id": self.turn_id, "seq": self.seq, "sentence": len(self.sentences),
                "mime": AUDIO_MIME, "data": "", "final": True, "stopped": self.stopped, "stages": self.stages()}

    def stages(self) -> dict[str, int]:
        """Durations in ms from the end of the user's speech to each stage reached so far
        (docs/voice-mode-plan.md §6 item 1). Empty without a speech_end stamp."""
        t0 = self.stamps.get("speech_end")
        if t0 is None:
            return {}
        return {k: round(v - t0) for k, v in self.stamps.items()
                if k != "speech_end" and isinstance(v, (int, float))}


# --------------------------------------------------------------------------- registry


@dataclass
class _Session:
    speaker: VoiceSpeaker
    reply_text: str | None = None  # the final text the worker persisted for this turn
    rewritten: bool = False


@dataclass
class SpeakerRegistry:
    """One live speaker per (agent, session); the per-session stop signal."""

    _speakers: dict[tuple[str | None, str], _Session] = field(default_factory=dict)
    _rewrites: dict[tuple[str | None, str], tuple[str, str]] = field(default_factory=dict)

    def start(self, speaker: VoiceSpeaker) -> asyncio.Task:
        key = (speaker.agent, speaker.session_id)
        old = self._speakers.pop(key, None)
        if old is not None and not old.speaker.stopped:
            old.speaker.stop()
        self._speakers[key] = _Session(speaker)
        return speaker.start()

    def set_reply(self, speaker: VoiceSpeaker, text: str) -> None:
        """The worker's final text for the turn, once handle() returns. A barge-in
        that came earlier (the model still streaming) is recorded now."""
        key = (speaker.agent, speaker.session_id)
        entry = self._speakers.get(key)
        if entry is None or entry.speaker is not speaker:
            return
        entry.reply_text = text
        if speaker.stopped:
            self._record_rewrite(key, entry, speaker.heard or "")

    def _record_rewrite(self, key, entry: _Session, heard: str) -> None:
        if entry.reply_text is None or entry.rewritten:
            return
        entry.rewritten = True
        self._rewrites[key] = (entry.reply_text, heard)

    def stop(self, session_id: str, agent: str | None = None, played_seq: int | None = None) -> bool:
        """Barge-in on the session's reply. True when there was a reply to interrupt.

        Three cases. The speaker is still running: stop it. It has finished
        sending but, by ``played_seq``, the client has not played it all (synthesis
        runs well ahead of playback, so this is the common case): nothing to
        stop, but the reply was interrupted and is rewritten. Already stopped,
        or played to the end: nothing."""
        keys = [k for k in self._speakers if k[1] == session_id and (agent is None or k[0] == agent)]
        interrupted = False
        for key in keys:
            entry = self._speakers[key]
            speaker = entry.speaker
            if speaker.stopped:
                continue
            if speaker._task is not None and speaker._task.done():
                heard = speaker.heard_prefix(played_seq)
                if played_seq is None or heard == " ".join(speaker.sentences):
                    continue  # played to the end, or the client did not say: nothing was cut off
                speaker.stopped = True
                speaker.heard = heard
                self._record_rewrite(key, entry, heard)
                interrupted = True
                continue
            speaker.stop(played_seq)
            interrupted = True
            self._record_rewrite(key, entry, speaker.heard or "")
        return interrupted

    def forget(self, session_id: str, agent: str | None = None) -> None:
        """A text turn follows: the voice reply before it can no longer be interrupted."""
        entry = self._speakers.pop((agent, session_id), None)
        if entry is not None and not entry.speaker.stopped and entry.speaker._task and not entry.speaker._task.done():
            entry.speaker.stop()

    def take_rewrite(self, session_id: str, agent: str | None = None) -> tuple[str, str] | None:
        """``(reply_text, heard_prefix)`` recorded by a stop, once."""
        return self._rewrites.pop((agent, session_id), None)


registry = SpeakerRegistry()


def rewrite_interrupted_reply(history: list[dict], reply_text: str, heard: str) -> bool:
    """Replace the last assistant message equal to ``reply_text`` with what was heard.

    The model then knows its reply was cut off (docs/voice-mode-plan.md §4).
    Returns True when a message was rewritten."""
    for m in reversed(history):
        if m.get("role") == "assistant" and (m.get("content") or "").strip() == reply_text.strip():
            m["content"] = f"{heard} {INTERRUPTED_MARKER}" if heard else UNHEARD_MARKER
            return True
    return False
