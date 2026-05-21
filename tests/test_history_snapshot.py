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


def test_attach_tool_call_reconstructs_attachment(tmp_path):
    """An `attach` tool call in the transcript is rebuilt into an attachment
    on the assistant entry, so a reopened conversation keeps its file."""
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    history = [
        {"role": "assistant", "content": "Here is the report.",
         "tool_calls": [
             {"function": {"name": "attach",
                           "arguments": json.dumps({"path": str(pdf)})}}
         ]},
    ]
    snap = _agent_with_history(history).history_snapshot()
    assert snap[0]["attachments"] == [{
        "filename": "report.pdf",
        "path": str(pdf),
        "mime_type": "application/pdf",
        "size": pdf.stat().st_size,
    }]


def test_no_attachments_key_when_no_attach_call():
    """Assistant entries without an attach call carry no `attachments` key."""
    history = [
        {"role": "assistant", "content": "",
         "tool_calls": [
             {"function": {"name": "bash",
                           "arguments": json.dumps({"command": "ls"})}}
         ]},
    ]
    snap = _agent_with_history(history).history_snapshot()
    assert "attachments" not in snap[0]


def test_attach_reconstruction_survives_missing_file(tmp_path):
    """If the attached file was later deleted, reconstruction still emits the
    metadata (size 0) — enrichment downstream flags it as missing."""
    gone = tmp_path / "gone.pdf"
    history = [
        {"role": "assistant", "content": "report",
         "tool_calls": [
             {"function": {"name": "attach",
                           "arguments": json.dumps({"path": str(gone)})}}
         ]},
    ]
    snap = _agent_with_history(history).history_snapshot()
    assert snap[0]["attachments"] == [{
        "filename": "gone.pdf",
        "path": str(gone),
        "mime_type": "application/pdf",
        "size": 0,
    }]
