"""POC: realtime voice with Gemini Live, reasoning delegated to GLM via OpenRouter,
plus a fully local voice mode (Whisper + Kokoro) with the same brain.

Browser <-WebSocket-> this server <-Gemini Live-> Gemini            (modes gemini, glm, always)
                                  <-HTTPS-> OpenRouter (GLM)        (ask_agent calls, or the local brain)
                                  <-threads-> Whisper + Kokoro      (mode local)

Browser protocol (one WebSocket at /ws?mode=gemini|glm|always|local):
  browser -> server  binary : 16-bit PCM, 16 kHz, mono microphone audio
  browser -> server  json   : {"type": "ping", "t_client"}            clock sync probe
                              {"type": "clock", "offset_ms", "rtt_ms"} browser's estimate of server - client clock
                              {"type": "speech_start"}                browser-side VAD: voice began
                              {"type": "speech_end", "t_client"}      browser-side VAD: last voice heard
                              {"type": "playback_start", "turn", "t_client"}
  server -> browser  binary : 16-bit PCM, 24 kHz, mono reply audio
  server -> browser  json   : {"type": "pong", ...}, {"type": "status", ...}, {"type": "event", ...},
                              {"type": "transcript", ...}, {"type": "audio_start", "turn"},
                              {"type": "clear"} (drop queued audio), {"type": "turn", ...} (latency summary)
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import AsyncIterator

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from google.genai import types

from local_voice import LocalSpeech, SentenceSplitter

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")
log = logging.getLogger("voice-poc")

GEMINI_MODEL = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.8-live")
GEMINI_VOICE = os.getenv("GEMINI_VOICE", "Kore")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "z-ai/glm-5.2")
# Any OpenAI-compatible chat completions endpoint: OpenRouter by default, or a local
# server such as Ollama (http://127.0.0.1:11434/v1) or LM Studio (http://127.0.0.1:1234/v1).
AGENT_BASE_URL = os.getenv("AGENT_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
OPENROUTER_URL = AGENT_BASE_URL + "/chat/completions"
GLM_TIMEOUT_S = float(os.getenv("GLM_TIMEOUT_S", "30"))
# BLOCKING: Gemini waits silently for the answer (cleanest latency measurement).
# NON_BLOCKING: Gemini may keep talking; the answer is delivered with scheduling=INTERRUPT.
ASK_AGENT_BLOCKING = os.getenv("ASK_AGENT_BEHAVIOR", "BLOCKING").upper() != "NON_BLOCKING"
LOCAL_STT_MODEL = os.getenv("LOCAL_STT_MODEL", "mlx-community/whisper-large-v3-turbo")
LOCAL_TTS_VOICE = os.getenv("LOCAL_TTS_VOICE", "af_heart")

MIC_MIME = "audio/pcm;rate=16000"
PREROLL_CHUNKS = 12  # 12 x 40 ms of audio kept before the browser VAD says speech started

PROMPT_GEMINI_ONLY = (
    "You are a friendly voice assistant. Keep answers short and conversational: "
    "one or two sentences unless the user asks for more."
)
PROMPT_WITH_AGENT = (
    "You are the voice of an assistant. Handle greetings, small talk and clarifying questions yourself. "
    "For any substantive question (facts, explanations, advice, anything that needs knowledge or reasoning) "
    "call ask_agent with the user's question as one self-contained sentence, then speak its answer to the "
    "user in your own words. Do not answer substantive questions yourself. Keep spoken answers concise. "
    "If ask_agent returns an error, tell the user briefly that the agent did not answer."
)
PROMPT_ALWAYS_DELEGATE = (
    "You are only the voice of an assistant; you have no knowledge of your own. On every user turn, without "
    "exception, call ask_agent with what the user said, as one self-contained message, then speak the answer "
    "it returns word for word. Never answer, greet, acknowledge or comment yourself, and never add anything "
    "to the agent's answer. If ask_agent returns an error, say only: the agent did not answer."
)
MODES = {"gemini": PROMPT_GEMINI_ONLY, "glm": PROMPT_WITH_AGENT, "always": PROMPT_ALWAYS_DELEGATE, "local": None}

GLM_SYSTEM = (
    "You are the reasoning engine behind a voice assistant. Your answer will be read aloud. "
    "Answer in plain prose, no markdown, no lists, at most four sentences unless more is essential."
)
AGENT_FAILED_LINE = "Sorry, the agent did not answer."


def now_ms() -> float:
    return time.time() * 1000.0


# --------------------------------------------------------------------------- latency


@dataclass
class Turn:
    """Timestamps (server clock, epoch ms) for one user turn and the response to it."""

    n: int
    speech_end: float | None = None  # last voice heard (browser VAD, converted to server clock)
    speech_end_source: str = ""
    turn_detected: float | None = None  # the turn detector's decision reached the server
    stt_done: float | None = None  # local mode: transcript ready
    tool_call: float | None = None
    glm_start: float | None = None
    glm_first_token: float | None = None
    glm_done: float | None = None
    first_sentence: float | None = None  # local mode: first sentence handed to TTS
    first_audio: float | None = None
    playback_start: float | None = None
    question: str = ""
    answer_chars: int = 0
    interrupted: bool = False

    def durations(self) -> dict[str, int]:
        d: dict[str, int] = {}

        def dur(key: str, a: float | None, b: float | None) -> None:
            if a is not None and b is not None:
                d[key] = round(b - a)

        dur("speech_end_to_turn_detected", self.speech_end, self.turn_detected)
        dur("stt", self.turn_detected, self.stt_done)
        dur("speech_end_to_tool_call", self.speech_end, self.tool_call)
        dur("glm_first_token", self.glm_start, self.glm_first_token)
        dur("glm_total", self.glm_start, self.glm_done)
        dur("tts_first", self.first_sentence, self.first_audio)
        dur("glm_done_to_first_audio", self.glm_done, self.first_audio)
        dur("speech_end_to_first_audio", self.speech_end, self.first_audio)
        dur("first_audio_to_playback", self.first_audio, self.playback_start)
        dur("speech_end_to_playback", self.speech_end, self.playback_start)
        return d

    def summary(self) -> dict:
        return {"type": "turn", **asdict(self), "durations": self.durations()}


def to_server_time(t_client: float, offset_ms: float) -> float:
    """Convert a browser Date.now() timestamp to the server clock."""
    return t_client + offset_ms


# --------------------------------------------------------------------------- GLM


class AgentError(Exception):
    pass


def parse_sse_line(line: str) -> tuple[str, str] | None:
    """Return ("content"|"reasoning", text) for an OpenRouter SSE data line, else None."""
    if not line.startswith("data:"):
        return None
    payload = line[5:].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        delta = json.loads(payload)["choices"][0].get("delta", {})
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    if delta.get("content"):
        return ("content", delta["content"])
    if delta.get("reasoning"):
        return ("reasoning", delta["reasoning"])
    return None


async def stream_chat(messages: list[dict], http: httpx.AsyncClient, api_key: str) -> AsyncIterator[tuple[str, str]]:
    """Stream ("content"|"reasoning", text) deltas from the agent model. Raises AgentError."""
    body = {"model": OPENROUTER_MODEL, "stream": True, "messages": messages}
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with http.stream("POST", OPENROUTER_URL, headers=headers, json=body, timeout=GLM_TIMEOUT_S) as r:
            if r.status_code != 200:
                raise AgentError(f"agent returned HTTP {r.status_code}")
            async for line in r.aiter_lines():
                parsed = parse_sse_line(line)
                if parsed is not None:
                    yield parsed
    except httpx.TimeoutException:
        raise AgentError(f"agent timed out after {GLM_TIMEOUT_S:.0f} seconds")
    except httpx.HTTPError as e:
        raise AgentError(f"agent request failed: {type(e).__name__}")


async def ask_glm(question: str, turn: Turn, http: httpx.AsyncClient, api_key: str) -> dict:
    """Complete GLM answer for a Gemini function call. Returns {"answer": ...} or {"error": ...}."""
    messages = [{"role": "system", "content": GLM_SYSTEM}, {"role": "user", "content": question}]
    turn.glm_start = now_ms()
    chunks: list[str] = []
    try:
        async for kind, text in stream_chat(messages, http, api_key):
            if kind == "content":
                if turn.glm_first_token is None:
                    turn.glm_first_token = now_ms()
                chunks.append(text)
    except AgentError as e:
        return {"error": str(e)}
    finally:
        turn.glm_done = now_ms()
    answer = "".join(chunks).strip()
    turn.answer_chars = len(answer)
    if not answer:
        return {"error": "agent returned an empty answer"}
    return {"answer": answer}


# --------------------------------------------------------------------------- sessions


class BrowserSession:
    """What every mode shares: the browser socket, clock sync, turn bookkeeping, events."""

    def __init__(self, ws: WebSocket, mode: str):
        self.ws = ws
        self.mode = mode
        self.offset_ms = 0.0
        self.turn = Turn(n=1)
        self.turns: dict[int, Turn] = {1: self.turn}
        self.turns_done = 0
        self.turns_delegated = 0

    async def send_json(self, obj: dict) -> None:
        await self.ws.send_text(json.dumps(obj))

    async def event(self, event_name: str, **extra) -> None:
        log.info("turn %d %s %s", self.turn.n, event_name, extra if extra else "")
        await self.send_json({"type": "event", "name": event_name, "t": now_ms(), "turn": self.turn.n, **extra})

    def new_turn(self) -> None:
        self.turn = Turn(n=self.turn.n + 1)
        self.turns[self.turn.n] = self.turn

    async def finish_turn(self, delegated: bool) -> None:
        self.turns_done += 1
        self.turns_delegated += delegated
        await self.event("turn_complete", delegated=delegated, delegated_turns=f"{self.turns_delegated}/{self.turns_done}")
        await self.send_json(self.turn.summary())
        self.new_turn()

    async def send_audio(self, pcm: bytes, t: float) -> None:
        if self.turn.first_audio is None:
            self.turn.first_audio = t
            await self.send_json({"type": "audio_start", "turn": self.turn.n})
            await self.event("first_audio_chunk")
        await self.ws.send_bytes(pcm)

    async def pump_browser(self) -> None:
        while True:
            msg = await self.ws.receive()
            if msg["type"] == "websocket.disconnect":
                return
            data = msg.get("bytes")
            if data is not None:
                await self.on_audio(data)
                continue
            text = msg.get("text")
            if text is not None:
                await self.on_browser_event(json.loads(text))

    async def on_audio(self, data: bytes) -> None:
        raise NotImplementedError

    async def on_browser_event(self, ev: dict) -> bool:
        """Handle the mode-independent messages; True when handled."""
        kind = ev.get("type")
        if kind == "ping":
            await self.send_json({"type": "pong", "t_client": ev["t_client"], "t_server": now_ms()})
        elif kind == "clock":
            self.offset_ms = float(ev["offset_ms"])
            await self.event("clock_synced", offset_ms=round(self.offset_ms), rtt_ms=ev.get("rtt_ms"))
        elif kind == "playback_start":
            turn = self.turns.get(ev.get("turn"))
            if turn and turn.playback_start is None:
                turn.playback_start = to_server_time(ev["t_client"], self.offset_ms)
                await self.send_json(turn.summary())
        else:
            return False
        return True


# --------------------------------------------------------------------------- Gemini


def live_config(mode: str) -> types.LiveConnectConfig:
    tools = None
    if mode != "gemini":
        decl = types.FunctionDeclaration(
            name="ask_agent",
            description="Ask the reasoning agent a question. Returns its complete text answer.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "question": types.Schema(type="STRING", description="The user's question, self-contained."),
                },
                required=["question"],
            ),
            behavior=types.Behavior.BLOCKING if ASK_AGENT_BLOCKING else types.Behavior.NON_BLOCKING,
        )
        tools = [types.Tool(function_declarations=[decl])]
    return types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=MODES[mode],
        tools=tools,
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=GEMINI_VOICE))
        ),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        # Audio-only sessions are capped at 15 minutes without compression.
        context_window_compression=types.ContextWindowCompressionConfig(sliding_window=types.SlidingWindow()),
    )


class VoiceSession(BrowserSession):
    """One browser connection bound to one Gemini Live session."""

    def __init__(self, ws: WebSocket, mode: str, gemini: genai.Client, http: httpx.AsyncClient, openrouter_key: str):
        super().__init__(ws, mode)
        self.gemini = gemini
        self.http = http
        self.openrouter_key = openrouter_key
        self.session: object = None
        self.cancelled: set[str] = set()
        self.tasks: set[asyncio.Task] = set()

    def spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def run(self) -> None:
        async with self.gemini.aio.live.connect(model=GEMINI_MODEL, config=live_config(self.mode)) as session:
            self.session = session
            await self.send_json({"type": "status", "state": "connected", "model": GEMINI_MODEL, "mode": self.mode,
                                  "glm_model": OPENROUTER_MODEL, "ask_agent_blocking": ASK_AGENT_BLOCKING})
            browser = asyncio.create_task(self.pump_browser())
            gemini = asyncio.create_task(self.pump_gemini())
            done, pending = await asyncio.wait({browser, gemini}, return_when=asyncio.FIRST_COMPLETED)
            for t in pending | self.tasks:
                t.cancel()
            for t in done:
                if t.exception():
                    log.warning("pump ended with %r", t.exception())

    async def on_audio(self, data: bytes) -> None:
        await self.session.send_realtime_input(audio=types.Blob(data=data, mime_type=MIC_MIME))

    async def on_browser_event(self, ev: dict) -> bool:
        if await super().on_browser_event(ev):
            return True
        if ev.get("type") == "speech_end" and self.turn.speech_end is None:
            self.turn.speech_end = to_server_time(ev["t_client"], self.offset_ms)
            self.turn.speech_end_source = "browser-vad"
            await self.event("speech_end", source="browser-vad")
        return True

    async def pump_gemini(self) -> None:
        while True:
            saw_turn_complete = False
            async for msg in self.session.receive():
                t = now_ms()
                if msg.setup_complete:
                    await self.event("gemini_setup_complete")
                if msg.voice_activity:
                    kind = str(msg.voice_activity.voice_activity_type)
                    if kind.endswith("ACTIVITY_END") and self.turn.turn_detected is None:
                        self.turn.turn_detected = t
                    await self.event("voice_activity", kind=kind)
                if msg.tool_call:
                    for fc in msg.tool_call.function_calls:
                        self.spawn(self.handle_tool_call(fc, t))
                if msg.tool_call_cancellation:
                    ids = list(msg.tool_call_cancellation.ids or [])
                    self.cancelled.update(ids)
                    await self.event("tool_call_cancelled", ids=ids)
                if msg.go_away:
                    await self.event("go_away", time_left=str(msg.go_away.time_left))
                sc = msg.server_content
                if sc:
                    saw_turn_complete = await self.on_server_content(sc, t) or saw_turn_complete
            if not saw_turn_complete:
                await self.event("gemini_closed")
                return

    async def on_server_content(self, sc, t: float) -> bool:
        if sc.interrupted:
            self.turn.interrupted = True
            await self.send_json({"type": "clear"})
            await self.event("interrupted")
            await self.send_json(self.turn.summary())
            self.new_turn()
        if sc.input_transcription and sc.input_transcription.text:
            await self.send_json({"type": "transcript", "who": "user", "text": sc.input_transcription.text, "turn": self.turn.n})
        if sc.output_transcription and sc.output_transcription.text:
            await self.send_json({"type": "transcript", "who": "gemini", "text": sc.output_transcription.text, "turn": self.turn.n})
        if sc.model_turn:
            for part in sc.model_turn.parts or []:
                if part.inline_data and part.inline_data.data:
                    await self.send_audio(part.inline_data.data, t)
        if sc.turn_complete:
            await self.finish_turn(delegated=self.turn.tool_call is not None)
            return True
        return False

    async def handle_tool_call(self, fc, t_received: float) -> None:
        turn = self.turn
        turn.tool_call = t_received
        turn.question = (fc.args or {}).get("question", "")
        await self.event("tool_call", function=fc.name, question=turn.question)
        if fc.name != "ask_agent":
            result = {"error": f"unknown function {fc.name}"}
        else:
            try:
                result = await ask_glm(turn.question, turn, self.http, self.openrouter_key)
            except Exception as e:  # never leave Gemini waiting on a crashed handler
                log.exception("ask_glm failed")
                result = {"error": f"agent call failed: {type(e).__name__}"}
        await self.event("glm_done", **{k: v for k, v in result.items() if k == "error"},
                         first_token_ms=turn.durations().get("glm_first_token"), total_ms=turn.durations().get("glm_total"))
        if fc.id in self.cancelled:
            await self.event("glm_result_dropped", reason="tool call cancelled")
            return
        response = types.FunctionResponse(id=fc.id, name=fc.name, response=result)
        if not ASK_AGENT_BLOCKING:
            response.scheduling = types.FunctionResponseScheduling.INTERRUPT
        await self.session.send_tool_response(function_responses=[response])


# --------------------------------------------------------------------------- local


class LocalVoiceSession(BrowserSession):
    """Mode local: browser VAD -> Whisper -> GLM token stream -> sentences -> Kokoro -> browser.

    Nothing spoken leaves the machine except the transcript sent to the agent model.
    """

    def __init__(self, ws: WebSocket, http: httpx.AsyncClient, api_key: str, speech: LocalSpeech):
        super().__init__(ws, "local")
        self.http = http
        self.api_key = api_key
        self.speech = speech
        self.history: list[dict] = [{"role": "system", "content": GLM_SYSTEM}]
        self.preroll: collections.deque[bytes] = collections.deque(maxlen=PREROLL_CHUNKS)
        self.utterance: list[bytes] | None = None
        self.turn_task: asyncio.Task | None = None

    async def run(self) -> None:
        await self.send_json({"type": "status", "state": "loading", "detail": "loading local speech models"})
        await self.speech.ready()
        await self.send_json({"type": "status", "state": "connected", "model": f"{LOCAL_STT_MODEL} + kokoro/{LOCAL_TTS_VOICE}",
                              "mode": "local", "glm_model": OPENROUTER_MODEL})
        try:
            await self.pump_browser()
        finally:
            if self.turn_task:
                self.turn_task.cancel()

    async def on_audio(self, data: bytes) -> None:
        self.preroll.append(data)
        if self.utterance is not None:
            self.utterance.append(data)

    async def on_browser_event(self, ev: dict) -> bool:
        if await super().on_browser_event(ev):
            return True
        kind = ev.get("type")
        if kind == "speech_start":
            if self.turn_task and not self.turn_task.done():
                await self.interrupt()
            self.utterance = list(self.preroll)
            await self.event("speech_start", source="browser-vad")
        elif kind == "speech_end" and self.utterance is not None:
            pcm = b"".join(self.utterance)
            self.utterance = None
            self.turn.speech_end = to_server_time(ev["t_client"], self.offset_ms)
            self.turn.speech_end_source = "browser-vad"
            self.turn.turn_detected = now_ms()
            await self.event("speech_end", source="browser-vad", audio_ms=len(pcm) // 32)
            self.turn_task = asyncio.create_task(self.respond(self.turn, pcm))
        return True

    async def interrupt(self) -> None:
        self.turn_task.cancel()
        await asyncio.gather(self.turn_task, return_exceptions=True)
        await self.send_json({"type": "clear"})
        self.turn.interrupted = True
        await self.event("interrupted")
        await self.send_json(self.turn.summary())
        self.new_turn()

    async def respond(self, turn: Turn, pcm: bytes) -> None:
        spoken: list[str] = []
        try:
            text = await asyncio.to_thread(self.speech.transcribe, pcm)
            turn.stt_done = now_ms()
            await self.event("stt_done", text=text)
            if not text:
                await self.finish_turn(delegated=False)
                return
            await self.send_json({"type": "transcript", "who": "user", "text": text, "turn": turn.n})
            turn.question = text
            self.history.append({"role": "user", "content": text})
            await self.generate(turn, spoken)
            turn.answer_chars = sum(len(s) for s in spoken)
            self.history.append({"role": "assistant", "content": " ".join(spoken)})
            await self.finish_turn(delegated=True)
        except asyncio.CancelledError:
            if spoken:
                self.history.append({"role": "assistant", "content": " ".join(spoken) + " [interrupted]"})
            raise
        except Exception:
            log.exception("local turn failed")
            await self.event("error", detail="local turn failed, see server log")
            await self.finish_turn(delegated=True)

    async def generate(self, turn: Turn, spoken: list[str]) -> None:
        """Stream the agent's answer into sentences; a consumer task speaks them as they complete."""
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        speaker = asyncio.create_task(self.speak_from(queue, turn, spoken))
        splitter = SentenceSplitter()
        turn.glm_start = now_ms()
        try:
            async for kind, delta in stream_chat(self.history, self.http, self.api_key):
                if kind != "content":
                    continue
                if turn.glm_first_token is None:
                    turn.glm_first_token = now_ms()
                    await self.event("glm_first_token")
                for sentence in splitter.feed(delta):
                    await queue.put(sentence)
            tail = splitter.flush()
            if tail:
                await queue.put(tail)
            if turn.glm_first_token is None:
                raise AgentError("agent returned an empty answer")
        except AgentError as e:
            await self.event("glm_error", detail=str(e))
            await queue.put(AGENT_FAILED_LINE)
        finally:
            turn.glm_done = now_ms()
            await queue.put(None)
            try:
                await speaker
            except asyncio.CancelledError:
                raise
        await self.event("glm_done", first_token_ms=turn.durations().get("glm_first_token"),
                         total_ms=turn.durations().get("glm_total"))

    async def speak_from(self, queue: asyncio.Queue, turn: Turn, spoken: list[str]) -> None:
        while (sentence := await queue.get()) is not None:
            if turn.first_sentence is None:
                turn.first_sentence = now_ms()
            await self.send_json({"type": "transcript", "who": "local", "text": sentence, "turn": turn.n})
            pcm = await asyncio.to_thread(self.speech.synthesize, sentence)
            await self.send_audio(pcm, now_ms())
            spoken.append(sentence)


# --------------------------------------------------------------------------- app

app = FastAPI(title="voice-poc")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
speech = LocalSpeech(LOCAL_STT_MODEL, LOCAL_TTS_VOICE)


@app.get("/")
async def index():
    return FileResponse(HERE / "static" / "index.html")


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, mode: str = "gemini"):
    await ws.accept()
    if mode not in MODES:
        mode = "gemini"
    gemini_key = os.getenv("GEMINI_API_KEY", "")
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "")
    needs_gemini = mode != "local"
    needs_agent_key = mode != "gemini" and "openrouter.ai" in AGENT_BASE_URL
    if (needs_gemini and not gemini_key) or (needs_agent_key and not openrouter_key):
        await ws.send_text(json.dumps({"type": "status", "state": "error",
                                       "detail": "GEMINI_API_KEY (modes A-C) and OPENROUTER_API_KEY (modes B-D) must be set"}))
        await ws.close()
        return
    async with httpx.AsyncClient() as http:
        try:
            if mode == "local":
                await LocalVoiceSession(ws, http, openrouter_key, speech).run()
            else:
                await VoiceSession(ws, mode, genai.Client(api_key=gemini_key), http, openrouter_key).run()
        except Exception as e:  # report instead of a silent close
            log.exception("session failed")
            try:
                await ws.send_text(json.dumps({"type": "status", "state": "error", "detail": f"{type(e).__name__}: {e}"}))
            except Exception:
                pass
    try:
        await ws.close()
    except Exception:
        pass


if __name__ == "__main__":
    import uvicorn

    # Console and a file next to this script, so the log can be read after the fact.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(HERE / "voice-poc.log")])
    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8787")))
