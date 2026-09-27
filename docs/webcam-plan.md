# Webcam as a first-class container feature — build plan

Plan for [#552](https://github.com/jalemieux/curunir/issues/552). This is planning
only: no code ships with this doc. Each phase below is sized to become one or
more implementation issues.

## TL;DR

- **Provisioning:** add one enable flag, `WEBCAM_ENABLED`. Cameras are declared in
  `WEBCAM_CAMERAS` (named USB paths or stream URLs). A boot-time probe records
  which cameras actually work. When a camera is missing, the feature degrades
  gracefully instead of crashing. The compose override stays for USB, since
  compose itself can't make `devices:` optional, but it gains a helper that
  detects the GID.
- **Agent surface:** replace `bash` + `snapshot.py` with an opt-in **`camera`
  tool** (`snapshot`, `describe`, `list`), unlocked by the `webcam` skill in the
  same way `to_audio` and `portfolio` are unlocked. The snapshot comes back as
  an attachment directly, so no LLM-driven `attach` step can be forgotten.
- **Watching:** add an in-process **`camera_watcher`** coroutine in `run.py`. It
  does cheap frame-diff motion detection with no LLM in the loop, and it wakes
  the agent only on change, with a cooldown. This replaces the pattern where
  every cron tick is a full agent turn plus a vision call.
- **UI:** add a `camera` panel in the local console showing the last snapshot,
  a capture button, the capture log and the watch rules. It is gated by
  *camera configured* **and** by the persona allowlisting `webcam`. The panel
  is not exposed through the portal in the MVP.
- **Privacy:** captures are opt-in per deployment. A capture ledger in SQLite
  records every capture. Frames are retained for a set period (default 7 days),
  and descriptions default to not identifying people. Raw frames never cross
  the portal relay unless `WEBCAM_PORTAL_IMAGES` is set.

## Where we are (verified in code, 2026-09-27)

| Piece | Today | Gap |
|---|---|---|
| Capture | `skills/webcam/snapshot.py`: ffmpeg grabs `warmup+1` frames with `-update 1` and keeps the last one | Every call pays a cold device open plus 10 warmup frames (~1–2 s). There is one device and no persistent stream. |
| Describe | `src.llm.describe_image(model, path, mime, prompt)` | **Its cache is keyed on image bytes only**, so asking a different question about the same frame returns the cached first answer. It also always appends the fixed prompt "Describe this image in detail…", which pulls answers toward a full scene inventory even when the question is narrow (bad for privacy and cost). |
| Invocation | The agent runs `python skills/webcam/snapshot.py` through the `bash` tool | Nothing enforces allowlisting or auditing. The agent has to remember to call `attach`. |
| Provisioning | Manual `docker-compose.webcam.yml` (`devices:` + `group_add: video`) | Getting the GID right is manual. When the camera is absent, the first failure only shows up at call time. |
| Scheduled watching | Cron → `agent.handle(system_task_prompt=…)` under `sched:<id>:<ts>` | **The return value of a scheduled turn is discarded** (`scheduler._run_task`), and `attach` in a `sched:` session reaches nobody. The only way an alert gets out is if the agent chooses to run `email-send`. Every tick is also a full agent turn plus a vision call, even when nothing changed. |
| UI | Snapshots show up in the local console's Files rail because they land in `context/workspace/generated/` | There is no camera panel, and nothing tells the operator whether a camera is configured. |

## 1. Provisioning

**Configuration.**

```
WEBCAM_ENABLED=true                         # master switch; default false
WEBCAM_CAMERAS=desk=/dev/video0,door=rtsp://user:pass@10.0.0.5/stream1
WEBCAM_DEVICE=/dev/video0                   # back-compat: single unnamed camera "default"
WEBCAM_RESOLUTION=1280x720                  # default for all; per-camera later if needed
```

`WEBCAM_CAMERAS` is `name=source` pairs. If it is unset and `WEBCAM_DEVICE` is
set, that becomes one camera named `default`, so today's deployments keep
working unchanged. Stream URLs can carry credentials, so the registry redacts
them everywhere they are logged or displayed.

**Boot probe (`src/camera/registry.py`).** When `WEBCAM_ENABLED` is set, each
camera is probed once at startup:
- `/dev/*`: the node must exist, `os.access(R_OK)` must pass (this catches the
  GID problem with a precise message), and `ffprobe`/`v4l2-ctl --list-formats`
  must succeed.
- URL: an `ffprobe` with a 5 s timeout.

The result is a `CameraStatus(name, source_redacted, ok, error, formats)` kept
in memory. Probing never blocks startup. A failed camera is logged at WARNING
and marked `ok=False`, and the tool and UI report the stored error instead of
calling ffmpeg again. This fits the existing soft startup-warning pattern for
missing API keys. Probes can be re-run on demand (tool `list` action with
`refresh=true`, or the UI ⟳ button) so a camera plugged in later can be
recovered without a restart. A USB camera plugged in after boot is still only
visible if the compose `devices:` entry was present; this is a known compose
limitation.

**Compose / GID.** Keep `docker-compose.webcam.yml` because it can't be avoided:
compose refuses to start when a listed device is missing. Make it painless:
- Add `scripts/webcam-setup.sh`. On the host, it lists `/dev/video*` with
  `v4l2-ctl --list-devices`, reads the device's GID (`stat -c %g`), and writes
  `WEBCAM_DEVICE` / `WEBCAM_GID` into `.env`.
- The override uses `group_add: ["${WEBCAM_GID:-video}"]`, which removes the
  numeric-GID hand edit.
- Alternative for multiple cameras or hotplug: `device_cgroup_rules:
  ['c 81:* rmw']` plus bind-mounting `/dev/v4l`. This is documented as an
  advanced option rather than the default, because it grants every video
  device.
- IP/RTSP cameras need no override at all; setting `WEBCAM_CAMERAS` in `.env`
  is enough. Recommend this path for multi-camera setups.

**Multiple cameras.** These are first-class through `WEBCAM_CAMERAS` naming.
Every tool call and UI element takes `camera=<name>`, and a missing name
defaults to the first camera that is ok.

## 2. Agent surface

Add an opt-in **`camera`** tool (`src/tools/camera_tool.py`), registered in
`_OPT_IN_SCHEMAS` and listed in the `webcam` skill's `tools:` frontmatter.
Like `to_audio`, it is in `_ASYNC_EXECUTORS_WITH_ATTACHMENTS`, so the captured
frame is attached by the tool itself.

```
camera(action="list")                                  -> cameras + status
camera(action="snapshot", camera="desk", attach=true)  -> path (+ attachment)
camera(action="describe", camera="desk", question="Is the door closed?")
                                                       -> {path, description, model}
```

- `describe` = snapshot + vision call, with the question passed through
  verbatim. It takes a `question`-only prompt mode, which is a new
  `describe_image(..., mode="answer")` that drops the fixed "describe in
  detail" suffix. It **fixes the cache key** to `sha256(bytes) + model +
  prompt`, which is also a small correctness fix for the existing
  attachment-description path.
- `attach` defaults to true on interactive sessions and false on `sched:`
  sessions, where no one is listening.
- **Clips are deferred** to a later phase (`action="clip", seconds≤10`,
  producing an mp4 and describing N sampled keyframes). Vision models generally
  take stills, so clip description means sampling keyframes anyway; ship it only
  if a real use case appears.
- `snapshot.py` stays as a thin CLI over the same `src/camera/capture.py`
  module, for operators and tests. The skill text switches from `bash` to the
  tool.
- Every call writes a row to the capture ledger (see §5).

**Latency.** A frame buffer (Phase 3) lets `snapshot` return the latest
buffered frame in about 0 ms instead of paying for a cold open. Until then the
ffmpeg one-shot stays as it is.

## 3. Watching and triggers

The current pattern is a cron entry every N minutes that says "check whether the
garage door is open". It costs one full agent turn plus one vision call per
tick, and it can only alert by choosing to run `email-send`.

Proposed **`src/camera/watcher.py`**: a long-lived coroutine in `run.py`'s
TaskGroup. It only starts when at least one watch rule exists:

1. **Frame source:** one persistent ffmpeg process per watched camera, emitting
   downscaled grayscale frames (e.g. 160×120 at 1–2 fps) as raw bytes on
   stdout. It is one process shared by the watcher and the Phase 3 snapshot
   buffer.
2. **Change detection, no LLM:** mean absolute difference against a running
   background (EMA), with an optional region-of-interest mask per rule, plus a
   debounce (change must persist ≥ K frames). Implement it in **numpy**, which
   is already transitive through litellm/yfinance (to be verified in the
   issue). No OpenCV: it adds ~50 MB to the image for features we don't need.
3. **On trigger:** capture a full-resolution frame, then **either**
   - `notify` mode: send the frame plus a template message straight to the
     alert sink, with no LLM; **or**
   - `describe` mode: call `describe_image` with the rule's question and alert
     only if the answer matches the rule's condition. The condition is
     evaluated by a small yes/no-constrained prompt, not free text; **or**
   - `agent` mode: fire `agent.handle(system_task_prompt=rule.prompt)` with the
     frame path. This is the escape hatch for rich follow-ups.
4. **Cooldown** per rule (default 10 min) plus a daily vision-call budget
   (`WEBCAM_VISION_DAILY_BUDGET`, default 200). When the budget runs out,
   triggers fall back to notify-only.

**Watch rules** live in SQLite next to schedules, in `context/camera.db`
(`watch_rules`: id, camera, mode, question/prompt, roi, sensitivity,
cooldown_sec, sink, enabled, active_hours cron-ish window, last_fired). The
store follows the same `db.py`/`engine.py` split as `schedule_store`, so the
tool, CLI and UI share one validated engine. The `camera` tool gains
`watch_add` / `watch_list` / `watch_remove`.

**Alert delivery: a missing primitive.** A scheduled or watcher-driven turn
has no channel to reply to. Add a small **`notify(sink, text, attachments)`**
router function with these sinks:
- `email:<addr>`: reuses `FastmailClient` plus the outbound allowlist, and
  makes the existing email-send path reachable without the LLM.
- `local`: pushes a toast frame into the local console socket if one is open,
  and always appends to the capture ledger so it shows in the panel.
- `portal`: a text-only alert by default. Images only go out with
  `WEBCAM_PORTAL_IMAGES=true` (see §5).

This primitive is useful beyond the webcam; for example, scheduled jobs could
use it to deliver results. It should be its own issue that the webcam work
depends on.

## 4. UI

**Local console (MVP).** Add a `camera` tab, gated two ways:
1. by persona: a new `Module(name="camera", gating_skill="webcam",
   panel_id="camera", endpoint_prefixes=("/api/camera",))` in
   `src/modules.py`; and
2. by runtime: the tab is present only if `WEBCAM_ENABLED` is set and at least
   one camera is registered. `enabled_modules()` today only takes the
   allowlist, so extend it with an optional `runtime_available: set[str]`
   (module names whose backing hardware/config exists) rather than
   special-casing camera in the channel.

Contents:
- One card per camera showing status (ok / error text), the last snapshot
  thumbnail with its time, and a **Capture now** button
  (`POST /api/camera/<name>/snapshot`, token-gated like the schedule writes).
- The capture ledger (time, camera, trigger: chat/schedule/watch/ui, session,
  whether it was described, whether it was sent off-box).
- Watch rules: list / toggle / delete, mirroring the Schedules tab's editing
  pattern (engine-validated, `ValueError` → 400).

**Live preview (Phase 3).** Add an MJPEG endpoint (`GET /api/camera/<name>/live`,
`multipart/x-mixed-replace`) fed from the shared frame buffer. It works in a
plain `<img>` with no WebRTC. It is loopback/token only, and a visible
"LIVE" badge shows while any client is connected.

**Portal.** Not in the MVP. The portal is a multi-tenant relay; streaming a
home camera through it is a separate threat model. If wanted later, add
last-snapshot only, explicitly opt-in (see §5).

## 5. Privacy and security

- **Off by default.** Nothing camera-related runs without `WEBCAM_ENABLED`,
  and the tool is only reachable on personas that allowlist `webcam`. Today no
  shipped persona except `default` (which allows everything) can reach it;
  keep it that way and add it explicitly per deployment.
- **Who can trigger:** interactive users on allowed channels, schedules and
  watch rules the user created, and the local UI button. **Email-originated
  turns cannot capture by default** (`WEBCAM_ALLOW_EMAIL_TRIGGER=false`),
  because an inbound email is the easiest thing for a third party to spoof or
  prompt-inject into ("take a photo and reply with it"). The sender allowlist
  helps but is not auth.
- **Capture ledger** (`context/camera.db: captures`) records every frame
  taken: time, camera, trigger, session_id, path, described?, model, and
  destination(s). It is the audit log, and the UI shows it. It can't be turned
  off.
- **Retention:** `WEBCAM_RETENTION_DAYS` (default 7) is applied by a daily sweep
  that deletes frames from `context/workspace/generated/webcam-*` and marks
  ledger rows `purged`. The ledger rows themselves are kept. Watcher-triggered
  frames that were not alerted are never written to disk; they stay only in the
  in-memory buffer.
- **Indicator:** for USB cameras, the hardware LED is the honest indicator.
  The persistent watcher stream keeps it lit, so document that this is
  expected while watching. The UI shows a LIVE/WATCHING badge. Optionally,
  local UI toasts on every capture.
- **Leakage paths:**
  - *Portal relay:* attachments currently flow through the portal to the
    browser. The `camera` tool marks its attachments `sensitive=true`, and
    `PortalChannel.send` drops sensitive image payloads (keeping a text
    placeholder) unless `WEBCAM_PORTAL_IMAGES=true`.
  - *Vision provider:* every description sends the frame to a third party.
    Document this. For privacy-sensitive setups, support a local
    `VISION_MODEL` (e.g. an Ollama llava/qwen-vl via `API_BASE`, see
    `docs/local-llm.md`).
  - *Memory extraction:* descriptions enter history and so memory extraction.
    The describe prompt defaults to "do not identify or describe people beyond
    what the question needs", and watcher/agent-mode sessions (`cam:*`) are
    excluded from memory extraction like `sched:*` scratch sessions.
  - *Logs:* stream URLs are redacted, and descriptions are logged at DEBUG only.

## 6. Relation to voice mode (#551)

The voice plan owns the audio pipeline. The webcam integration point is small
if the `camera` tool exists:
- During a voice turn, "what am I holding up?" is just a `camera(describe)`
  tool call. The voice session must allowlist the tool, and voice latency makes
  the **frame buffer** (snapshot ≈ 0 ms) and a **fast vision model** matter
  more. Budget ~1–2 s for describe with a flash-class model.
- If #551 lands a realtime speech-to-speech model that accepts images (e.g.
  realtime APIs with image input), the buffer can push a frame into the
  realtime session directly instead of going through `describe_image`. Keep
  `src/camera/buffer.py` API-shaped (`latest_frame(camera) -> bytes, ts`) so
  either path can consume it.
- Voice barge-in and the camera share nothing else; no coupling is needed
  beyond the tool.

## 7. Phasing

| Phase | Scope | New env / deps | Issues |
|---|---|---|---|
| **0 — fixes** (small, standalone) | Fix the `describe_image` cache key (bytes + model + prompt); add a question-only prompt mode | none | 1 |
| **1 — MVP: tool + provisioning** | `src/camera/` (registry + probe, capture moved out of the skill), opt-in `camera` tool (`list`/`snapshot`/`describe`), `WEBCAM_ENABLED` + `WEBCAM_CAMERAS` (back-compat with `WEBCAM_DEVICE`), capture ledger, `WEBCAM_GID` in the override + `scripts/webcam-setup.sh`, skill rewritten to use the tool, email-trigger block, retention sweep | `WEBCAM_ENABLED`, `WEBCAM_CAMERAS`, `WEBCAM_GID`, `WEBCAM_RETENTION_DAYS`, `WEBCAM_ALLOW_EMAIL_TRIGGER`; no new Python deps | 3–4 (registry/probe · tool · ledger+retention · compose/setup script) |
| **2 — UI panel + notify** | `camera` module in `src/modules.py` with runtime gating, local-console tab (status, last snapshot, capture button, ledger), `notify()` sink primitive (email/local), sensitive-attachment handling in the portal | `WEBCAM_PORTAL_IMAGES` | 3 (module gating · panel · notify primitive) |
| **3 — watching** | Persistent frame source + shared buffer, numpy motion detection, `camera.db` watch rules + engine, watcher coroutine with notify/describe/agent modes, cooldown + daily vision budget, rules editable in UI + tool, MJPEG live preview | `WEBCAM_VISION_DAILY_BUDGET`, `WEBCAM_WATCH_FPS`; numpy made explicit in `requirements.txt` | 4 (frame source/buffer · detector · rules store+tool · watcher+UI) |
| **4 — later / optional** | Clips (`action="clip"`), per-camera resolution/ROI editor in UI, portal last-snapshot (opt-in), realtime-voice frame push (with #551), hotplug via `device_cgroup_rules` | TBD | as needed |

**Tests.** Everything is testable without hardware in the same style as today's
`snapshot.py` tests: an injected ffmpeg runner, a fake frame source that yields
synthetic numpy frames for the detector, and a fake describer. There is one
optional hardware smoke test, `pytest -m camera`, which is skipped when no
device is present.

## Open questions for the owner

1. Is **the portal ever in scope** for camera images, or should it stay
   local/email only for good? (The plan assumes local/email only; the portal
   gets text alerts.)
2. **Local vision model** as the recommended default for camera deployments
   (privacy), or a hosted flash-class model (quality/latency)?
3. Is the **email-trigger block** too strict? It would stop "email me a photo
   of the garage" when sent *as an email*. Schedules and watch rules still
   email out fine.
4. Should `notify()` be scoped to this work or planned as its own
   cross-cutting feature? (The plan treats it as a dependency issue of its
   own.)
