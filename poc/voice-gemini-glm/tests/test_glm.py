import asyncio
import json

import httpx

import server
from server import Turn, ask_glm, parse_sse_line


def sse(*events: str) -> bytes:
    return "".join(f"data: {e}\n\n" for e in events).encode()


def test_parse_sse_line():
    assert parse_sse_line(': OPENROUTER PROCESSING') is None
    assert parse_sse_line('data: [DONE]') is None
    assert parse_sse_line('data: {"choices":[{"delta":{"content":"Hi"}}]}') == ("content", "Hi")
    assert parse_sse_line('data: {"choices":[{"delta":{"reasoning":"hmm"}}]}') == ("reasoning", "hmm")
    assert parse_sse_line('data: {"choices":[{"delta":{}}]}') is None
    assert parse_sse_line('data: not json') is None


def run(handler, question="q"):
    turn = Turn(n=1)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await ask_glm(question, turn, http, "key")

    return asyncio.run(go()), turn


def test_streams_answer_and_records_first_token():
    seen = {}

    def handler(request: httpx.Request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = request.read()
        return httpx.Response(200, content=sse(
            '{"choices":[{"delta":{"reasoning":"thinking"}}]}',
            '{"choices":[{"delta":{"content":"Paris "}}]}',
            '{"choices":[{"delta":{"content":"is it."}}]}',
            '[DONE]'))

    result, turn = run(handler, "capital of France?")
    assert result == {"answer": "Paris is it."}
    assert seen["auth"] == "Bearer key"
    body = json.loads(seen["body"])
    assert body["stream"] is True and body["messages"][-1]["content"] == "capital of France?"
    assert turn.glm_start <= turn.glm_first_token <= turn.glm_done
    assert turn.answer_chars == len("Paris is it.")


def test_http_error_becomes_error_result():
    result, turn = run(lambda r: httpx.Response(500, content=b"boom"))
    assert result == {"error": "agent returned HTTP 500"}
    assert turn.glm_done is not None and turn.glm_first_token is None


def test_timeout_becomes_error_result():
    def handler(request):
        raise httpx.ReadTimeout("slow")

    result, _ = run(handler)
    assert result["error"].startswith("agent timed out")


def test_empty_answer_is_an_error():
    result, _ = run(lambda r: httpx.Response(200, content=sse('{"choices":[{"delta":{}}]}', '[DONE]')))
    assert result == {"error": "agent returned an empty answer"}
