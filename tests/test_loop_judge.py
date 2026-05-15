# tests/test_loop_judge.py
import json
from unittest.mock import AsyncMock, patch

import pytest

from src.agent.loop_judge import (
    JudgeDecision,
    judge_loop_progress,
    summarize_recent_iterations,
)
from src.llm import LLMResponse


def _tool_call(tid: str, name: str, args: dict) -> dict:
    return {
        "id": tid,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def _build_history(n_turns: int, original_request: str = "do the thing") -> list[dict]:
    """Build a synthetic history with one user message and N assistant+tool pairs."""
    hist: list[dict] = [{"role": "user", "content": original_request}]
    for i in range(n_turns):
        tid = f"call_{i}"
        hist.append({
            "role": "assistant",
            "tool_calls": [_tool_call(tid, "bash", {"command": f"echo {i}"})],
        })
        hist.append({
            "role": "tool",
            "tool_call_id": tid,
            "content": f"output {i}",
        })
    return hist


class TestSummarizeRecentIterations:
    def test_includes_user_request_and_last_n_turns(self):
        hist = _build_history(30, original_request="original prompt here")
        summary = summarize_recent_iterations(hist, last_n=10)
        # original user request is prepended verbatim
        assert "original prompt here" in summary
        # Only last 10 iterations should appear — earliest iterations excluded
        assert "echo 29" in summary
        assert "echo 20" in summary
        assert "echo 19" not in summary

    def test_truncates_long_tool_results(self):
        hist: list[dict] = [{"role": "user", "content": "hi"}]
        hist.append({
            "role": "assistant",
            "tool_calls": [_tool_call("c1", "bash", {"command": "yes"})],
        })
        hist.append({
            "role": "tool",
            "tool_call_id": "c1",
            "content": "X" * 5000,
        })
        summary = summarize_recent_iterations(hist, last_n=10)
        # 5000 chars should be truncated down to ~500 in the summary
        assert "X" * 1000 not in summary
        # And it should mention truncation
        assert "truncated" in summary.lower()

    def test_handles_history_shorter_than_last_n(self):
        hist = _build_history(3)
        summary = summarize_recent_iterations(hist, last_n=10)
        # Should still produce a valid summary with all 3 turns
        assert "echo 0" in summary
        assert "echo 2" in summary

    def test_extracts_first_text_block_from_multimodal_user_message(self):
        """When the user message is a multimodal list, extract the first text block."""
        hist: list[dict] = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "describe the image"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ],
        }]
        summary = summarize_recent_iterations(hist, last_n=10)
        assert "describe the image" in summary


class TestJudgeLoopProgress:
    async def test_continue_decision_parses(self):
        resp = LLMResponse(
            text=json.dumps({
                "decision": "continue",
                "rationale": "almost done",
                "summary_if_stopping": "tried A, B, C",
            }),
            tool_calls=None,
        )
        with patch("src.agent.loop_judge.call_llm", new_callable=AsyncMock, return_value=resp):
            decision = await judge_loop_progress(
                model="test-model",
                api_base=None,
                openrouter_provider=None,
                user_request="do something",
                recent_transcript="(empty)",
                iterations_so_far=75,
                extension_number=0,
                tokens_so_far=10_000,
            )
        assert isinstance(decision, JudgeDecision)
        assert decision.action == "continue"
        assert decision.rationale == "almost done"
        assert decision.summary == "tried A, B, C"

    async def test_stop_decision_parses(self):
        resp = LLMResponse(
            text=json.dumps({
                "decision": "stop",
                "rationale": "going in circles",
                "summary_if_stopping": "tried X, Y but failed",
            }),
            tool_calls=None,
        )
        with patch("src.agent.loop_judge.call_llm", new_callable=AsyncMock, return_value=resp):
            decision = await judge_loop_progress(
                model="test-model",
                api_base=None,
                openrouter_provider=None,
                user_request="do something",
                recent_transcript="(empty)",
                iterations_so_far=75,
                extension_number=0,
                tokens_so_far=10_000,
            )
        assert decision.action == "stop"
        assert decision.rationale == "going in circles"
        assert "tried X" in decision.summary

    async def test_unparseable_output_fails_safe_to_stop(self):
        resp = LLMResponse(text="this is not JSON at all", tool_calls=None)
        with patch("src.agent.loop_judge.call_llm", new_callable=AsyncMock, return_value=resp):
            decision = await judge_loop_progress(
                model="test-model",
                api_base=None,
                openrouter_provider=None,
                user_request="do something",
                recent_transcript="(empty)",
                iterations_so_far=75,
                extension_number=0,
                tokens_so_far=10_000,
            )
        # Fail-safe is stop, not continue
        assert decision.action == "stop"
        assert "unparseable" in decision.rationale.lower()

    async def test_handles_fenced_json(self):
        resp = LLMResponse(
            text=(
                "Here is my decision:\n"
                "```json\n"
                + json.dumps({
                    "decision": "continue",
                    "rationale": "still progressing",
                    "summary_if_stopping": "partial work",
                })
                + "\n```\n"
                "End of response."
            ),
            tool_calls=None,
        )
        with patch("src.agent.loop_judge.call_llm", new_callable=AsyncMock, return_value=resp):
            decision = await judge_loop_progress(
                model="test-model",
                api_base=None,
                openrouter_provider=None,
                user_request="do something",
                recent_transcript="(empty)",
                iterations_so_far=75,
                extension_number=0,
                tokens_so_far=10_000,
            )
        assert decision.action == "continue"
        assert decision.rationale == "still progressing"

    async def test_empty_response_fails_safe_to_stop(self):
        resp = LLMResponse(text=None, tool_calls=None)
        with patch("src.agent.loop_judge.call_llm", new_callable=AsyncMock, return_value=resp):
            decision = await judge_loop_progress(
                model="test-model",
                api_base=None,
                openrouter_provider=None,
                user_request="do something",
                recent_transcript="(empty)",
                iterations_so_far=75,
                extension_number=0,
                tokens_so_far=10_000,
            )
        assert decision.action == "stop"
