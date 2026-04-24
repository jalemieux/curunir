"""End-to-end integration: CLI → ws → agent_worker → mocked LLM."""
import asyncio
import base64
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import websockets

from src.agent.agent import Agent
from src.channels.ws import WebSocketChannel
from src.config import AgentConfig
from src.llm import LLMResponse
import run as run_module


@pytest.mark.asyncio
async def test_cli_upload_end_to_end(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # so context/uploads/ is under tmp_path
    identity = tmp_path / "context" / "identity.md"
    identity.parent.mkdir(parents=True, exist_ok=True)
    identity.write_text("You are a test assistant.")
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir()
    config = AgentConfig(
        identity_file=identity,
        context_dir=tmp_path / "context",
        skill_dirs=[skill_dir],
    )
    agent = Agent(config)

    in_q: asyncio.Queue = asyncio.Queue()
    out_q: asyncio.Queue = asyncio.Queue()
    ch = WebSocketChannel(in_q, host="127.0.0.1", port=19400, model=config.model)

    llm_calls: list[list] = []

    async def fake_call_llm(model, messages, tools, **kwargs):
        llm_calls.append(messages)
        return LLMResponse(text="thanks for the picture", tool_calls=None)

    with patch("src.agent.agent.call_llm", new=fake_call_llm):
        server_task = asyncio.create_task(ch.start())
        worker_task = asyncio.create_task(run_module.agent_worker(agent, in_q, out_q))
        await asyncio.sleep(0.1)  # let server bind

        try:
            async with websockets.connect("ws://127.0.0.1:19400") as ws:
                png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
                payload = {
                    "content": "describe",
                    "command": None,
                    "attachments": [{
                        "filename": "t.png",
                        "mime_type": "image/png",
                        "data": base64.b64encode(png).decode(),
                    }],
                }
                await ws.send(json.dumps(payload))
                # First message from server is the welcome (model: ...)
                # then we wait for out_q to get the actual reply
                reply = await asyncio.wait_for(out_q.get(), timeout=3.0)
                assert reply.content == "thanks for the picture"

                user_msg = [m for m in llm_calls[-1] if m["role"] == "user"][-1]
                assert isinstance(user_msg["content"], list)
                assert any(b.get("type") == "image_url" for b in user_msg["content"])

                uploads = tmp_path / "context" / "uploads" / "cli"
                assert uploads.exists()
        finally:
            worker_task.cancel()
            server_task.cancel()
            for t in (worker_task, server_task):
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
