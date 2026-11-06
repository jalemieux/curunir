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


class FakeSpeech:
    def __init__(self, text="what is the capital of france"):
        self.text = text
        self.synthesized = []

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
        await s.on_browser_event({"type": "speech_start"})
        await s.on_audio(b"\x00" * 1280)
        await s.on_browser_event({"type": "speech_end", "t_client": 1000.0})
        await asyncio.sleep(0.05)  # let the first sentence reach TTS
        await s.on_browser_event({"type": "speech_start"})  # barge-in
        assert s.turn_task.cancelled() or s.turn_task.done()

    asyncio.run(go())
    assert s.speech.synthesized == ["First sentence here."]
    assert {"type": "clear"} in s.ws.sent
    assert s.turns[1].interrupted and s.turn.n == 2
    assert s.history[-1]["content"] == "First sentence here. [interrupted]"
    assert s.utterance is not None  # the interrupting speech is being captured
