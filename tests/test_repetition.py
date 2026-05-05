# tests/test_repetition.py
"""Tests for the per-session tool-call repetition detector."""

from src.agent.repetition import (
    RepetitionDetector,
    Verdict,
    normalize_signature,
)


# Mirror the map in src/agent/agent.py — keep tests independent of import path.
KEY_ARGS = {
    "web_fetch": ["url"],
    "bash": ["command"],
    "write": ["file_path"],
    "read": ["file_path"],
    "edit": ["file_path"],
    "glob": ["pattern"],
    "grep": ["pattern"],
    "load_skill": ["name"],
    "delegate": ["task"],
    "attach": ["path"],
    "brave_search": ["query"],
}


class TestNormalizeSignature:
    def test_collapses_punctuation_casing_and_term_order(self):
        a = normalize_signature(
            "brave_search",
            {"query": '"Monique Morelli" mort décès 1993'},
            KEY_ARGS["brave_search"],
        )
        b = normalize_signature(
            "brave_search",
            {"query": "Monique morelli, 1993 décès, MORT"},
            KEY_ARGS["brave_search"],
        )
        assert a == b

    def test_signature_includes_tool_name(self):
        a = normalize_signature("read", {"file_path": "src/agent.py"}, KEY_ARGS["read"])
        b = normalize_signature("write", {"file_path": "src/agent.py"}, KEY_ARGS["write"])
        assert a[0] == "read"
        assert b[0] == "write"
        assert a != b

    def test_truncates_long_values(self):
        long_value = "x " * 1000  # 2000 chars
        sig = normalize_signature(
            "bash", {"command": long_value}, KEY_ARGS["bash"]
        )
        # token set should be just {"x"} since all tokens are "x" after split/dedupe
        assert sig[1] == frozenset({"x"})

    def test_unknown_tool_falls_back_to_first_arg(self):
        sig = normalize_signature("mystery", {"foo": "bar baz"}, [])
        assert sig[0] == "mystery"
        assert "bar" in sig[1] and "baz" in sig[1]

    def test_empty_args_yield_empty_token_set(self):
        sig = normalize_signature("bash", {}, KEY_ARGS["bash"])
        assert sig == ("bash", frozenset())

    def test_handles_non_string_values(self):
        sig = normalize_signature("custom", {"x": 42}, ["x"])
        assert "42" in sig[1]


class TestRepetitionDetectorExact:
    def _detector(self):
        return RepetitionDetector(key_args=KEY_ARGS)

    def test_first_two_identical_calls_are_ok(self):
        d = self._detector()
        args = {"url": "https://example.com"}
        assert d.observe("web_fetch", args) == Verdict.OK
        assert d.observe("web_fetch", args) == Verdict.OK

    def test_third_identical_call_nudges(self):
        d = self._detector()
        args = {"url": "https://example.com"}
        d.observe("web_fetch", args)
        d.observe("web_fetch", args)
        assert d.observe("web_fetch", args) == Verdict.NUDGE

    def test_block_after_excessive_repetition(self):
        d = self._detector()
        args = {"url": "https://example.com"}
        verdicts = [d.observe("web_fetch", args) for _ in range(10)]
        # First two OK, calls 3..9 NUDGE, 10th BLOCK.
        assert verdicts[0] == Verdict.OK
        assert verdicts[1] == Verdict.OK
        for v in verdicts[2:9]:
            assert v == Verdict.NUDGE
        assert verdicts[9] == Verdict.BLOCK

    def test_distinct_tools_with_same_args_do_not_collide(self):
        d = self._detector()
        for _ in range(5):
            assert d.observe("read", {"file_path": "x"}) in (Verdict.OK, Verdict.NUDGE)
        # write with same path should start fresh
        assert d.observe("write", {"file_path": "x"}) == Verdict.OK

    def test_clear_resets_state(self):
        d = self._detector()
        args = {"url": "https://example.com"}
        for _ in range(3):
            d.observe("web_fetch", args)
        d.clear()
        assert d.observe("web_fetch", args) == Verdict.OK


class TestRepetitionDetectorSimilar:
    """Near-duplicate (Jaccard) detection across the sliding window."""

    BRAVE_QUERIES = [
        '"Monique Morelli" mort décès 1993',
        'Monique Morelli mort 1993',
        '"Monique Morelli" décès 1993 chanteuse',
        'Monique Morelli morte 1993',
        '"Monique Morelli" décédée 1993',
        '"Monique Morelli" 1993 décès chanteuse',
    ]

    def test_consecutive_near_duplicates_trigger_nudge(self):
        d = RepetitionDetector(key_args=KEY_ARGS)
        verdicts = [
            d.observe("brave_search", {"query": q}) for q in self.BRAVE_QUERIES
        ]
        # By the 5th near-duplicate the detector should fire a nudge.
        assert any(v == Verdict.NUDGE for v in verdicts[:5])

    def test_dissimilar_calls_stay_ok(self):
        d = RepetitionDetector(key_args=KEY_ARGS)
        # Five completely unrelated queries — token sets share nothing.
        queries = [
            "weather Paris tomorrow",
            "best Italian restaurant Rome",
            "python asyncio tutorial 2024",
            "linux kernel scheduling latency",
            "barcelona football transfer rumors",
        ]
        verdicts = [d.observe("brave_search", {"query": q}) for q in queries]
        assert all(v == Verdict.OK for v in verdicts)
