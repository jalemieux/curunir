import asyncio
import json
from types import SimpleNamespace

import server
from server import VoiceSession


class FakeWs:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))


class FakeLive:
    def __init__(self):
        self.responses = []

    async def send_tool_response(self, function_responses):
        self.responses.extend(function_responses)


def make_session():
    s = VoiceSession(FakeWs(), "glm", gemini=None, http=None, openrouter_key="k")
    s.session = FakeLive()
    return s


def test_tool_call_round_trip(monkeypatch):
    async def fake_glm(question, turn, http, key):
        turn.glm_first_token = turn.glm_done = turn.glm_start = 1.0
        return {"answer": f"answer to {question}"}

    monkeypatch.setattr(server, "ask_glm", fake_glm)
    s = make_session()
    fc = SimpleNamespace(id="call-1", name="ask_agent", args={"question": "why?"})
    asyncio.run(s.handle_tool_call(fc, 100.0))

    names = [m["name"] for m in s.ws.sent if m["type"] == "event"]
    assert names == ["tool_call", "glm_done"]
    assert s.ws.sent[0]["function"] == "ask_agent" and s.ws.sent[0]["question"] == "why?"
    (resp,) = s.session.responses
    assert resp.id == "call-1" and resp.name == "ask_agent" and resp.response == {"answer": "answer to why?"}
    assert s.turn.tool_call == 100.0 and s.turn.question == "why?"


def test_cancelled_call_result_is_dropped(monkeypatch):
    async def fake_glm(question, turn, http, key):
        return {"answer": "late"}

    monkeypatch.setattr(server, "ask_glm", fake_glm)
    s = make_session()
    s.cancelled.add("call-2")
    asyncio.run(s.handle_tool_call(SimpleNamespace(id="call-2", name="ask_agent", args={"question": "q"}), 1.0))
    assert s.session.responses == []
    assert [m["name"] for m in s.ws.sent if m["type"] == "event"][-1] == "glm_result_dropped"


def test_crashed_glm_call_reports_error_to_gemini(monkeypatch):
    async def fake_glm(question, turn, http, key):
        raise RuntimeError("boom")

    monkeypatch.setattr(server, "ask_glm", fake_glm)
    s = make_session()
    asyncio.run(s.handle_tool_call(SimpleNamespace(id="c", name="ask_agent", args={"question": "q"}), 1.0))
    (resp,) = s.session.responses
    assert resp.response == {"error": "agent call failed: RuntimeError"}
