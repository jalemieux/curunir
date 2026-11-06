# tests/test_voice_speaker.py
"""The speaker of a voice turn (#551 §3, §4): sentences spoken in order as
audio_chunk frames, the per-session stop, and the heard-prefix rewrite."""
import asyncio
import base64
import json
from unittest.mock import patch

import pytest

from run import agent_worker
from src.agent.agent import Agent
from src.channels.base import IncomingMessage, OutgoingMessage
from src.llm import LLMResponse
from src.voice import speaker as speaker_mod
from src.voice.speaker import (
    INTERRUPTED_MARKER,
    UNHEARD_MARKER,
    SpeakerRegistry,
    VoiceSpeaker,
    rewrite_interrupted_reply,
)


class FakeTTS:
    """Two 'slices' per sentence, after a short delay; records what it was asked."""

    def __init__(self, delay=0.01, fail_on=None):
        self.delay, self.fail_on = delay, fail_on
        self.asked: list[str] = []

    async def stream(self, text):
        self.asked.append(text)
        await asyncio.sleep(self.delay)
        if text == self.fail_on:
            raise RuntimeError("tts down")
        yield text.encode()[:4]
        yield text.encode()[4:8] or b"\x00\x00"


def collector():
    frames: list[dict] = []

    async def emit(frame):
        frames.append(frame)

    return frames, emit


def data(frame):
    return base64.b64decode(frame["data"])


async def test_sentences_are_spoken_in_order_with_text_on_the_first_slice():
    frames, emit = collector()
    tts = FakeTTS()
    sp = VoiceSpeaker(tts, emit, session_id="s", agent="a", turn_id="t1")
    task = sp.start()
    for d in ["Hello there", ", friend. Second", " one here. Third!"]:
        sp.feed(d)
    sp.finish("Hello there, friend. Second one here. Third!")
    await asyncio.wait_for(task, 5)

    assert tts.asked == ["Hello there, friend.", "Second one here.", "Third!"]
    assert [f["seq"] for f in frames] == list(range(1, 8))
    assert [f["sentence"] for f in frames] == [0, 0, 1, 1, 2, 2, 3]
    assert [f.get("text") for f in frames] == ["Hello there, friend.", None, "Second one here.", None, "Third!", None, None]
    assert frames[-1] == {"type": "audio_chunk", "turn_id": "t1", "seq": 7, "sentence": 3,
                          "mime": "audio/pcm;rate=24000", "data": "", "final": True, "stopped": False, "stages": {}}
    assert all(f["mime"] == "audio/pcm;rate=24000" and f["turn_id"] == "t1" for f in frames)
    assert data(frames[0]) == b"Hell"
    assert sp.last_seq_of_sentence == {0: 2, 1: 4, 2: 6}


async def test_unstreamed_final_text_is_spoken_and_streamed_text_is_not_repeated():
    frames, emit = collector()
    tts = FakeTTS()
    sp = VoiceSpeaker(tts, emit, session_id="s")
    sp.start()
    sp.feed("All good.")
    sp.finish("All good.")  # same text as streamed: nothing more
    await asyncio.wait_for(sp._task, 5)
    assert tts.asked == ["All good."]

    frames, emit = collector()
    tts = FakeTTS()
    sp = VoiceSpeaker(tts, emit, session_id="s")
    sp.start()
    sp.finish("Sorry, I encountered an error processing your message.")  # never streamed
    await asyncio.wait_for(sp._task, 5)
    assert tts.asked == ["Sorry, I encountered an error processing your message."]

    tts = FakeTTS()
    sp = VoiceSpeaker(tts, emit, session_id="s")
    sp.start()
    sp.finish("(interrupted)")
    await asyncio.wait_for(sp._task, 5)
    assert tts.asked == []


async def test_markdown_is_made_speakable_and_empty_sentences_skipped():
    frames, emit = collector()
    tts = FakeTTS()
    sp = VoiceSpeaker(tts, emit, session_id="s")
    sp.start()
    sp.feed("Here it is:\n```\ncode\n```\nUse **this** one. 🎉")
    sp.finish(None)
    await asyncio.wait_for(sp._task, 5)
    assert tts.asked == ["Here it is:", "I've put the code in the chat.", "Use this one."]


async def test_synthesis_failure_skips_the_sentence_not_the_turn():
    frames, emit = collector()
    tts = FakeTTS(fail_on="Two is bad.")
    sp = VoiceSpeaker(tts, emit, session_id="s")
    sp.start()
    sp.feed("One, two. Two is bad. Three four.")
    sp.finish(None)
    await asyncio.wait_for(sp._task, 5)
    assert [f.get("text") for f in frames if f.get("text")] == ["One, two.", "Three four."]
    assert frames[-1]["final"] is True


async def test_stop_drops_queued_sentences_and_records_what_was_heard():
    frames, emit = collector()
    tts = FakeTTS(delay=0.05)
    sp = VoiceSpeaker(tts, emit, session_id="s", lookahead=1)
    task = sp.start()
    sp.feed("First one here. Second one here. Third one here. Fourth one here.")
    sp.finish(None)
    await asyncio.sleep(0.08)  # first sentence out, second in flight, two queued
    assert [f["sentence"] for f in frames if not f["final"]] == [0, 0]
    sp.stop(played_seq=2)
    await asyncio.wait_for(task, 5)

    assert frames[-1]["final"] is True and frames[-1]["stopped"] is True
    assert [f["sentence"] for f in frames[:-1]] == [0, 0]  # nothing after the stop
    assert tts.asked == ["First one here.", "Second one here."]  # queued ones never synthesized
    assert sp.heard == "First one here."


async def test_heard_prefix_follows_the_played_seq():
    frames, emit = collector()
    sp = VoiceSpeaker(FakeTTS(), emit, session_id="s")
    sp.start()
    sp.feed("Alpha beta gamma. Delta epsilon zeta. Eta theta.")
    sp.finish(None)
    await asyncio.wait_for(sp._task, 5)
    assert sp.heard_prefix(None) == "Alpha beta gamma. Delta epsilon zeta. Eta theta."
    assert sp.heard_prefix(4) == "Alpha beta gamma. Delta epsilon zeta."
    assert sp.heard_prefix(3) == "Alpha beta gamma."
    assert sp.heard_prefix(0) == ""


async def test_registry_stops_the_live_speaker_and_hands_over_the_rewrite():
    reg = SpeakerRegistry()
    frames, emit = collector()
    sp = VoiceSpeaker(FakeTTS(delay=0.05), emit, session_id="portal", agent="default")
    reg.start(sp)
    text = "One two three. Four five six. Seven eight nine. Ten eleven twelve."
    sp.feed(text)
    sp.finish(text)
    reg.set_reply(sp, text)
    await asyncio.sleep(0.08)  # two sentences out, two still synthesizing
    assert reg.stop("other", "default") is False
    assert reg.stop("portal", "default", played_seq=2) is True
    assert reg.stop("portal", "default") is False  # already stopped
    assert reg.take_rewrite("portal", "default") == (text, "One two three.")
    assert reg.take_rewrite("portal", "default") is None
    await asyncio.wait_for(sp._task, 5)


async def test_registry_without_agent_stops_any_agent_and_a_fully_played_reply_is_not_a_stop():
    reg = SpeakerRegistry()
    frames, emit = collector()
    sp = VoiceSpeaker(FakeTTS(), emit, session_id="cli", agent="coach")
    reg.start(sp)
    sp.finish("Done now.")
    reg.set_reply(sp, "Done now.")
    await asyncio.wait_for(sp._task, 5)
    last_data_seq = max(f["seq"] for f in frames if f["data"])
    assert reg.stop("cli", played_seq=last_data_seq) is False  # played to the end
    assert reg.stop("cli") is False  # no played_seq: the client did not say anything was cut off
    assert reg.take_rewrite("cli", "coach") is None


async def test_stages_count_from_the_end_of_speech_into_the_closing_frame():
    import time

    frames, emit = collector()
    t0 = time.time() * 1000.0 - 1000
    sp = VoiceSpeaker(FakeTTS(), emit, session_id="s", stamps={"speech_end": t0, "transcript": t0 + 200})
    sp.start()
    sp.feed("Hello there friend.")
    sp.finish(None)
    await asyncio.wait_for(sp._task, 5)
    stages = frames[-1]["stages"]
    assert stages["transcript"] == 200
    assert 1000 <= stages["first_audio_chunk_sent"] <= stages["audio_final"] < 3000
    assert VoiceSpeaker(FakeTTS(), emit, session_id="s").stages() == {}  # no speech_end: nothing to count from


async def test_a_text_turn_forgets_the_previous_voice_reply():
    reg = SpeakerRegistry()
    frames, emit = collector()
    sp = VoiceSpeaker(FakeTTS(), emit, session_id="s", agent="a")
    reg.start(sp)
    sp.finish("One two three. Four five six.")
    reg.set_reply(sp, "One two three. Four five six.")
    await asyncio.wait_for(sp._task, 5)
    reg.forget("s", "a")  # the session moved on to a text turn
    assert reg.stop("s", "a", played_seq=1) is False and reg.take_rewrite("s", "a") is None


async def test_barge_in_after_synthesis_finished_but_before_playback_ended_is_recorded():
    """Synthesis runs ahead of playback: the speaker task is done for most of a reply."""
    reg = SpeakerRegistry()
    frames, emit = collector()
    sp = VoiceSpeaker(FakeTTS(), emit, session_id="portal", agent="default")
    reg.start(sp)
    text = "One two three. Four five six. Seven eight nine."
    sp.feed(text)
    sp.finish(text)
    reg.set_reply(sp, text)
    await asyncio.wait_for(sp._task, 5)
    assert reg.stop("portal", "default", played_seq=2) is True
    assert reg.take_rewrite("portal", "default") == (text, "One two three.")
    assert reg.stop("portal", "default", played_seq=2) is False  # once


async def test_barge_in_while_the_model_still_streams_is_recorded_when_the_reply_lands():
    reg = SpeakerRegistry()
    frames, emit = collector()
    sp = VoiceSpeaker(FakeTTS(delay=0.05), emit, session_id="s", agent="a")
    reg.start(sp)
    sp.feed("One two three. Four five six. ")
    await asyncio.sleep(0.08)  # first sentence out, model still streaming
    assert reg.stop("s", "a", played_seq=2) is True
    assert reg.take_rewrite("s", "a") is None  # the worker has not persisted the reply yet
    reg.set_reply(sp, "One two three. Four five six. Seven eight nine.")  # handle() returns later
    assert reg.take_rewrite("s", "a") == ("One two three. Four five six. Seven eight nine.", "One two three.")
    await asyncio.wait_for(sp._task, 5)


async def test_first_sentence_is_spoken_while_the_model_is_still_streaming():
    """The look-ahead must not hold a synthesized sentence back waiting for the next one."""
    frames, emit = collector()
    sp = VoiceSpeaker(FakeTTS(delay=0.01), emit, session_id="s")
    sp.start()
    sp.feed("First sentence here. Sec")  # the model parks here
    await asyncio.sleep(0.1)
    assert [f["sentence"] for f in frames] == [0, 0]
    sp.feed("ond sentence here.")
    sp.finish(None)
    await asyncio.wait_for(sp._task, 5)
    assert [f["sentence"] for f in frames] == [0, 0, 1, 1, 2]


async def test_a_new_speaker_on_the_same_session_stops_the_old_one():
    reg = SpeakerRegistry()
    frames, emit = collector()
    old = VoiceSpeaker(FakeTTS(delay=0.05), emit, session_id="s", agent="a")
    reg.start(old)
    old.feed("One two three. Four five six.")
    old.finish(None)
    new = VoiceSpeaker(FakeTTS(), emit, session_id="s", agent="a")
    reg.start(new)
    assert old.stopped is True
    new.finish("Hi.")
    await asyncio.wait_for(asyncio.gather(old._task, new._task), 5)


def test_rewrite_interrupted_reply():
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "A. B. C."},
               {"role": "user", "content": "more"}, {"role": "assistant", "content": "A. B. C."}]
    assert rewrite_interrupted_reply(history, "A. B. C.", "A. B.") is True
    assert history[3]["content"] == f"A. B. {INTERRUPTED_MARKER}" and history[1]["content"] == "A. B. C."
    assert rewrite_interrupted_reply(history, "nope", "x") is False
    assert rewrite_interrupted_reply(history, "A. B. C.", "") is True
    assert history[1]["content"] == UNHEARD_MARKER


# ---- the worker


async def _voice_turn(agent_config, chunks, tts, *, voice=True):
    async def fake_llm(*args, on_text_delta=None, **kwargs):
        for c in chunks:
            await on_text_delta(c)
        return LLMResponse(text="".join(chunks), tool_calls=None)

    class Providers:
        pass

    providers = Providers()
    providers.tts = tts
    in_q, out_q = asyncio.Queue(), asyncio.Queue()
    await in_q.put(IncomingMessage(content="hi", channel="local_web", session_id="s1", reply_address={}, voice=voice))
    frames: list[OutgoingMessage] = []
    with patch("src.agent.agent.call_llm", new=fake_llm), patch("run.get_voice_providers", return_value=providers):
        agent = Agent(agent_config)
        task = asyncio.create_task(agent_worker(agent, in_q, out_q))
        try:
            deadline = asyncio.get_event_loop().time() + 5
            while asyncio.get_event_loop().time() < deadline:
                try:
                    f = await asyncio.wait_for(out_q.get(), timeout=0.3)
                except asyncio.TimeoutError:
                    break
                frames.append(f)
                if f.audio and f.audio.get("final"):
                    break
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    return agent, frames


async def test_worker_speaks_a_voice_turn_after_the_text_final(agent_config):
    tts = FakeTTS()
    agent, frames = await _voice_turn(agent_config, ["Hello ", "there. ", "Bye now."], tts)
    text_final = next(i for i, f in enumerate(frames) if f.final)
    audio = [f for f in frames if f.audio]
    assert tts.asked == ["Hello there.", "Bye now."]
    assert audio and audio[-1].audio["final"] is True
    assert all(f.content == "" and f.final is False and f.agent == "default" and f.channel == "local_web" for f in audio)
    assert frames[text_final].content == "Hello there. Bye now."
    deltas = [f.content for f in frames if f.delta]
    assert deltas == ["Hello ", "there. ", "Bye now."]


async def test_worker_stamps_first_text_delta_and_reports_voice_stats(agent_config):
    import time

    async def fake_llm(*args, on_text_delta=None, **kwargs):
        await on_text_delta("Hi there.")
        return LLMResponse(text="Hi there.", tool_calls=None)

    class Providers:
        pass

    providers = Providers()
    providers.tts = FakeTTS()
    in_q, out_q = asyncio.Queue(), asyncio.Queue()
    t0 = time.time() * 1000.0 - 500
    await in_q.put(IncomingMessage(content="hi", channel="cli", session_id="s1", reply_address={}, voice=True,
                                   voice_stamps={"speech_end": t0, "transcript": t0 + 100}))
    frames = []
    with patch("src.agent.agent.call_llm", new=fake_llm), patch("run.get_voice_providers", return_value=providers):
        task = asyncio.create_task(agent_worker(Agent(agent_config), in_q, out_q))
        try:
            while True:
                f = await asyncio.wait_for(out_q.get(), 5)
                frames.append(f)
                if f.audio and f.audio.get("final"):
                    break
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    final = next(f for f in frames if f.final and not f.audio)
    assert final.stats["voice"]["transcript"] == 100 and final.stats["voice"]["first_text_delta"] >= 500
    assert frames[-1].audio["stages"]["first_audio_chunk_sent"] >= 500


async def test_worker_text_turn_has_no_audio(agent_config):
    tts = FakeTTS()
    agent, frames = await _voice_turn(agent_config, ["Hi."], tts, voice=False)
    assert tts.asked == [] and not any(f.audio for f in frames)


async def test_worker_rewrites_the_interrupted_reply_before_the_next_turn(agent_config, monkeypatch):
    monkeypatch.setattr(speaker_mod.registry, "_speakers", {})
    monkeypatch.setattr(speaker_mod.registry, "_rewrites", {})
    tts = FakeTTS(delay=0.05)
    text = "First one here. Second one here. Third one here."

    async def fake_llm(*args, on_text_delta=None, **kwargs):
        await on_text_delta(text)
        return LLMResponse(text=text, tool_calls=None)

    class Providers:
        pass

    providers = Providers()
    providers.tts = tts
    in_q, out_q = asyncio.Queue(), asyncio.Queue()
    await in_q.put(IncomingMessage(content="hi", channel="local_web", session_id="s1", reply_address={}, voice=True))
    with patch("src.agent.agent.call_llm", new=fake_llm), patch("run.get_voice_providers", return_value=providers):
        agent = Agent(agent_config)
        task = asyncio.create_task(agent_worker(agent, in_q, out_q))
        try:
            while True:  # wait for the text final and the first audio slice
                f = await asyncio.wait_for(out_q.get(), 5)
                if f.audio and f.audio["sentence"] == 0 and not f.audio["final"]:
                    break
            # Barge-in through the container's cancel path, heard the first sentence
            from src.voice.speaker import registry
            assert registry.stop("s1", "default", played_seq=2) is True
            await in_q.put(IncomingMessage(content="again", channel="local_web", session_id="s1", reply_address={}))
            while True:
                f = await asyncio.wait_for(out_q.get(), 5)
                if f.final and not f.audio:
                    finals = [m for m in agent.sessions["s1"] if m.get("role") == "assistant"]
                    if len(finals) >= 2:
                        break
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assistant = [m["content"] for m in agent.sessions["s1"] if m.get("role") == "assistant"]
    assert assistant[0] == f"First one here. {INTERRUPTED_MARKER}"
    assert assistant[1] == text
