# tests/test_voice_listener.py
"""Audio in (#614): mic frames become transcribed voice turns."""
import base64

import pytest

from src.voice import listener as listener_mod
from src.voice.listener import Listeners, VoiceListener, handle_voice_frame


class FakeSTT:
    def __init__(self, text="what time is it", fail=False):
        self.text, self.fail = text, fail
        self.heard: list[bytes] = []

    async def transcribe(self, pcm):
        self.heard.append(pcm)
        if self.fail:
            raise RuntimeError("stt down")
        return self.text if pcm else ""


class FakeDetector:
    silence_ms = 500

    def __init__(self, script):
        self.script = list(script)
        self.resets = 0

    def feed(self, pcm):
        return self.script.pop(0) if self.script else None

    def reset(self):
        self.resets += 1


class FakeProviders:
    def __init__(self, stt=None, detector=None):
        self.stt = stt or FakeSTT()
        self._detector = detector

    def turn_detector(self):
        return self._detector


def harness(providers, channel="portal"):
    emitted, enqueued, cancels = [], [], []

    async def emit(frame):
        emitted.append(frame)

    async def enqueue(msg):
        enqueued.append(msg)

    listener = VoiceListener(providers, channel=channel, session_id="s1", agent="default",
                             enqueue=enqueue, emit=emit, cancel=lambda: cancels.append(1))
    return listener, emitted, enqueued, cancels


async def test_client_endpointing_without_a_server_detector():
    providers = FakeProviders()
    lst, emitted, enqueued, cancels = harness(providers)
    await lst.ready()
    assert emitted[-1] == {"type": "voice_state", "state": "ready", "server_vad": False, "provider": "unknown",
                           "session_id": "s1", "agent": "default"}
    await lst.audio(b"\x01" * 1280)
    await lst.audio(b"\x02" * 1280)
    assert enqueued == []
    await lst.audio_end()
    assert enqueued == []  # transcription runs in its own task; the receive loop is free
    await lst.flush()
    assert providers.stt.heard == [b"\x01" * 1280 + b"\x02" * 1280]
    transcript = next(f for f in emitted if f["type"] == "transcript")
    assert transcript["text"] == "what time is it" and transcript["audio_ms"] == 80 and transcript["vad_silence_ms"] == 0
    (msg,) = enqueued
    assert (msg.content, msg.channel, msg.session_id, msg.agent, msg.voice) == ("what time is it", "portal", "s1", "default", True)
    assert set(msg.voice_stamps) == {"speech_end", "transcript"} and msg.voice_stamps["transcript"] >= msg.voice_stamps["speech_end"]
    assert cancels == []  # barge-in is the client's call, in both modes
    await lst.audio_end()  # nothing buffered: no second turn
    await lst.flush()
    assert len(enqueued) == 1


async def test_server_detector_ends_turns_and_barges_in_on_start():
    det = FakeDetector([None, "start", None, "end"])
    providers = FakeProviders(detector=det)
    lst, emitted, enqueued, cancels = harness(providers, channel="local_web")
    await lst.ready()
    assert emitted[-1]["server_vad"] is True
    await lst.audio(b"\x00" * 1280)  # preroll only
    for _ in range(3):
        await lst.audio(b"\x01" * 1280)
    await lst.flush()
    assert cancels == []  # the client interrupts on "listening", with its played_seq
    assert [f["state"] for f in emitted if f["type"] == "voice_state"] == ["ready", "listening", "transcribing"]
    assert providers.stt.heard == [b"\x00" * 1280 + b"\x01" * 1280 * 3]  # preroll included
    assert enqueued[0].voice is True and enqueued[0].channel == "local_web"
    transcript = next(f for f in emitted if f["type"] == "transcript")
    assert transcript["vad_silence_ms"] == 500
    assert enqueued[0].voice_stamps["speech_end"] <= enqueued[0].voice_stamps["transcript"] - 0  # end counted back by the silence window
    lst.close()
    assert det.resets == 1


async def test_two_utterances_keep_their_order_and_do_not_block_each_other():
    import asyncio

    class SlowSTT:
        def __init__(self):
            self.calls = []

        async def transcribe(self, pcm):
            self.calls.append(pcm)
            await asyncio.sleep(0.05 if pcm[:1] == b"\x01" else 0.0)
            return "first" if pcm[:1] == b"\x01" else "second"

    providers = FakeProviders(stt=SlowSTT())
    lst, emitted, enqueued, _ = harness(providers)
    await lst.audio(b"\x01" * 1280)
    t = asyncio.get_event_loop().time()
    await lst.audio_end()
    assert asyncio.get_event_loop().time() - t < 0.02  # did not wait for STT
    await lst.audio(b"\x02" * 1280)
    await lst.audio_end()
    await lst.flush()
    assert [m.content for m in enqueued] == ["first", "second"]


async def test_silence_and_failures_leave_no_turn_and_return_to_ready():
    providers = FakeProviders(stt=FakeSTT(text=""))
    lst, emitted, enqueued, _ = harness(providers)
    await lst.audio(b"\x00" * 1280)
    await lst.audio_end()
    await lst.flush()
    assert enqueued == [] and emitted[-1]["state"] == "ready"

    providers = FakeProviders(stt=FakeSTT(fail=True))
    lst, emitted, enqueued, _ = harness(providers)
    await lst.audio(b"\x00" * 1280)
    await lst.audio_end()
    await lst.flush()
    assert enqueued == []
    assert next(f for f in emitted if f["type"] == "transcript")["text"] == ""


async def test_handle_voice_frame_opens_routes_and_closes(monkeypatch):
    monkeypatch.setattr(listener_mod, "listeners", Listeners())
    providers = FakeProviders()
    emitted, enqueued = [], []

    async def emit(frame):
        emitted.append(frame)

    async def enqueue(msg):
        enqueued.append(msg)

    kw = dict(channel="cli", session_id="cli", agent=None, enqueue=enqueue, emit=emit, cancel=lambda: None, providers=providers)
    assert await handle_voice_frame({"content": "hello"}, **kw) is False
    assert await handle_voice_frame({"type": "audio", "data": "AAAA"}, **kw) is True
    assert emitted[-1]["state"] == "off"  # audio before voice_on
    assert await handle_voice_frame({"type": "audio", "data": "AAAA"}, **kw) is True
    assert len(emitted) == 1  # said once, not per audio frame
    assert await handle_voice_frame({"type": "voice_on"}, **kw) is True
    import asyncio
    await asyncio.sleep(0.02)  # the listener opens in a task
    assert emitted[-1]["state"] == "ready"
    pcm = b"\x01" * 1280
    assert await handle_voice_frame({"type": "audio", "data": base64.b64encode(pcm).decode()}, **kw) is True
    assert await handle_voice_frame({"type": "audio_end"}, **kw) is True
    await listener_mod.listeners.get("cli", "cli", None).flush()
    assert providers.stt.heard == [pcm] and enqueued[0].content == "what time is it"
    assert await handle_voice_frame({"type": "voice_off"}, **kw) is True
    assert listener_mod.listeners.get("cli", "cli", None) is None
    assert await handle_voice_frame({"type": "audio", "data": "bad base64!"}, **kw) is True  # dropped quietly


async def test_voice_on_warms_the_models_in_a_task_and_drops_audio_meanwhile(monkeypatch):
    import asyncio

    reg = Listeners()
    monkeypatch.setattr(listener_mod, "listeners", reg)
    emitted, order = [], []

    async def emit(frame):
        emitted.append(frame)

    class Warm(FakeProviders):
        needs_warm = True

        async def warm(self):
            order.append("warm")
            await asyncio.sleep(0.05)

    kw = dict(channel="portal", session_id="p", agent=None, enqueue=None, emit=emit, cancel=lambda: None)
    t = asyncio.get_event_loop().time()
    await handle_voice_frame({"type": "voice_on"}, providers=Warm(), **kw)
    assert asyncio.get_event_loop().time() - t < 0.02  # the receive loop did not wait
    assert reg.loading("portal", "p", None)
    await handle_voice_frame({"type": "audio", "data": "AAAA"}, **kw)  # dropped, no "off"
    await asyncio.sleep(0.1)
    assert [f["state"] for f in emitted] == ["loading", "ready"] and order == ["warm"]
    assert not reg.loading("portal", "p", None) and reg.get("portal", "p", None) is not None

    class Broken(FakeProviders):
        needs_warm = True

        async def warm(self):
            raise RuntimeError("no model")

    emitted.clear()
    await handle_voice_frame({"type": "voice_on"}, channel="portal", session_id="q", agent=None,
                             enqueue=None, emit=emit, cancel=lambda: None, providers=Broken())
    await asyncio.sleep(0.02)
    assert [f["state"] for f in emitted] == ["loading", "unavailable"]

    emitted.clear()  # hosted provider: nothing to load, no "loading" flash
    await handle_voice_frame({"type": "voice_on"}, channel="portal", session_id="r", agent=None,
                             enqueue=None, emit=emit, cancel=lambda: None, providers=FakeProviders())
    await asyncio.sleep(0.02)
    assert [f["state"] for f in emitted] == ["ready"]

    # voice_off while loading cancels the warm-up
    emitted.clear()
    await handle_voice_frame({"type": "voice_on"}, providers=Warm(), **kw)
    await handle_voice_frame({"type": "voice_off"}, **kw)
    await asyncio.sleep(0.1)
    assert not reg.loading("portal", "p", None) and reg.get("portal", "p", None) is None
    assert "ready" not in [f["state"] for f in emitted]  # cancelled before it opened


async def test_handle_voice_frame_reports_unavailable_providers(monkeypatch):
    monkeypatch.setattr(listener_mod, "listeners", Listeners())
    emitted = []

    async def emit(frame):
        emitted.append(frame)

    async def enqueue(msg):
        pass

    def boom():
        raise RuntimeError("no key")

    monkeypatch.setattr("src.voice.providers.get_providers", boom)
    await handle_voice_frame({"type": "voice_on"}, channel="portal", session_id="p", agent=None,
                             enqueue=enqueue, emit=emit, cancel=lambda: None)
    assert emitted == [{"type": "voice_state", "state": "unavailable", "session_id": "p"}]


def test_listeners_registry_replaces_and_closes_per_channel():
    reg = Listeners()
    det = FakeDetector([])
    a = VoiceListener(FakeProviders(detector=det), channel="portal", session_id="s", agent=None,
                      enqueue=None, emit=None, cancel=None)
    reg.open(a)
    b = VoiceListener(FakeProviders(), channel="portal", session_id="s", agent=None, enqueue=None, emit=None, cancel=None)
    reg.open(b)
    assert det.resets == 1 and reg.get("portal", "s", None) is b
    reg.close_channel("portal")
    assert reg.get("portal", "s", None) is None
