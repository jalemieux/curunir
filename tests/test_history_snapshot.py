import json

import pytest

from src.agent.agent import Agent


def _agent_with_history(history):
    """Construct a minimal Agent and stuff history into the 'portal' session."""
    a = Agent.__new__(Agent)  # bypass __init__ if expensive
    a.sessions = {"portal": history}
    return a


def test_user_and_assistant_turns_kept():
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    snap = _agent_with_history(history).history_snapshot()
    assert snap == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello", "tool_calls": []},
    ]


def test_tool_role_is_dropped():
    history = [
        {"role": "user", "content": "x"},
        {"role": "tool", "content": "internal"},
        {"role": "assistant", "content": "done"},
    ]
    snap = _agent_with_history(history).history_snapshot()
    roles = [m["role"] for m in snap]
    assert "tool" not in roles


def test_tool_calls_are_summarized():
    history = [
        {"role": "assistant", "content": "",
         "tool_calls": [
             {"function": {"name": "bash", "arguments": json.dumps({"command": "ls -la"})}}
         ]},
    ]
    snap = _agent_with_history(history).history_snapshot()
    assert snap[0]["tool_calls"] == ["bash: ls -la"]


def test_cap_at_200_messages():
    history = [{"role": "user", "content": str(i)} for i in range(250)]
    snap = _agent_with_history(history).history_snapshot()
    assert len(snap) <= 200


def test_user_multimodal_text_extracted():
    history = [{"role": "user", "content": [
        {"type": "text", "text": "look at this"},
        {"type": "image_url", "image_url": {"url": "data:..."}},
    ]}]
    snap = _agent_with_history(history).history_snapshot()
    assert snap[0]["content"] == "look at this"
