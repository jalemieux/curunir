import asyncio
import json

import server
from server import AgentError, LocalVoiceSession


class FakeWs:
    def __init__(self):
        self.sent = []
        self.audio = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    async def send_bytes(self, b):
        self.audio.append(b)


class FakeVad:
    silence_ms = 500

    def __init__(self):
        self.script = []

    def feed(self, pcm):
        return self.script.pop(0) if self.script else None


class FakeSpeech:
    def __init__(self, text="what is the capital of france"):
        self.text = text
        self.synthesized = []
        self.vad = FakeVad()

    def turn_detector(self):
        return self.vad

    def transcribe(self, pcm):
        return self.text if pcm else ""

    def synthesize(self, sentence):
        self.synthesized.append(sentence)
        return b"\x00\x01" * 10


def fake_stream(*deltas, error=None):
    async def stream(messages, http, api_key):
        stream.messages = list(messages)
        for d in deltas:
            yield ("content", d)
            await asyncio.sleep(0)
        if error:
            raise AgentError(error)

    return stream


def make(speech=None):
    s = LocalVoiceSession(FakeWs(), http=None, api_key="k", speech=speech or FakeSpeech())
    s.vad = s.speech.turn_detector()
    return s


def events(s):
    return [m["name"] for m in s.ws.sent if m["type"] == "event"]


def test_local_turn_streams_sentences_to_tts(monkeypatch):
    stream = fake_stream("Paris is the capital", " of France. It is on", " the Seine.")
    monkeypatch.setattr(server, "stream_chat", stream)
    s = make()
    asyncio.run(s.respond(s.turn, b"\x00" * 32000))

    assert s.speech.synthesized == ["Paris is the capital of France.", "It is on the Seine."]
    assert len(s.ws.audio) == 2
    assert events(s) == ["stt_done", "glm_first_token", "first_audio_chunk", "glm_done", "turn_complete"]
    assert [m["text"] for m in s.ws.sent if m["type"] == "transcript"] == [
        "what is the capital of france", "Paris is the capital of France.", "It is on the Seine."]
    assert stream.messages[-1] == {"role": "user", "content": "what is the capital of france"}
    assert s.history[-1] == {"role": "assistant", "content": "Paris is the capital of France. It is on the Seine."}
    done = s.turns[1]
    assert done.stt_done and done.glm_first_token and done.first_sentence and done.first_audio and done.glm_done
    assert done.answer_chars == len("Paris is the capital of France.") + len("It is on the Seine.")
    assert s.turn.n == 2


def test_agent_error_is_spoken(monkeypatch):
    monkeypatch.setattr(server, "stream_chat", fake_stream(error="agent returned HTTP 500"))
    s = make()
    asyncio.run(s.respond(s.turn, b"\x00" * 32000))
    assert s.speech.synthesized == [server.AGENT_FAILED_LINE]
    assert "glm_error" in events(s) and events(s)[-1] == "turn_complete"


def test_silence_ends_turn_without_agent_call(monkeypatch):
    called = []

    async def stream(messages, http, api_key):
        called.append(1)
        yield ("content", "x")

    monkeypatch.setattr(server, "stream_chat", stream)
    s = make(FakeSpeech(text=""))
    asyncio.run(s.respond(s.turn, b""))
    assert called == [] and events(s) == ["stt_done", "turn_complete"]


def test_speech_start_interrupts_running_turn(monkeypatch):
    async def slow_stream(messages, http, api_key):
        yield ("content", "First sentence here. ")
        await asyncio.sleep(10)
        yield ("content", "Never spoken.")

    monkeypatch.setattr(server, "stream_chat", slow_stream)
    s = make()

    async def go():
        s.vad.script = ["start", None, "end"]
        for _ in range(3):
            await s.on_audio(b"\x00" * 1280)
        assert s.turns[1].speech_end_source == "silero-vad"
        assert s.turns[1].turn_detected - s.turns[1].speech_end == 500
        await asyncio.sleep(0.05)  # let the first sentence reach TTS
        s.vad.script = ["start"]
        await s.on_audio(b"\x00" * 1280)  # barge-in
        assert s.turn_task.cancelled() or s.turn_task.done()

    asyncio.run(go())
    assert s.speech.synthesized == ["First sentence here."]
    assert {"type": "clear"} in s.ws.sent
    assert s.turns[1].interrupted and s.turn.n == 2
    assert s.history[-1]["content"] == "First sentence here. [interrupted]"
    assert s.utterance is not None  # the interrupting speech is being captured


def test_speech_start_after_finished_turn_clears_browser_playback():
    s = make()
    s.vad.script = ["start"]
    asyncio.run(s.on_audio(b"\x00" * 1280))
    assert {"type": "clear"} in s.ws.sent and s.utterance is not None


def test_browser_vad_events_are_ignored_in_local_mode():
    s = make()
    asyncio.run(s.on_browser_event({"type": "speech_start"}))
    asyncio.run(s.on_browser_event({"type": "speech_end", "t_client": 1.0}))
    assert s.utterance is None and s.turn_task is None
