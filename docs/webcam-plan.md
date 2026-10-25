# Webcam as a first-class container feature — build plan

Plan for [#552](https://github.com/jalemieux/curunir/issues/552). This is planning
only: no code ships with this doc. Each phase below is sized to become one or
more implementation issues.

## TL;DR

- **Provisioning is a conversation.** Curunir is equipped to find every
  camera on the private network its host is on and every camera on the host's
  USB ports. It asks the user **which ones are off limits**, and saves the
  rest **in its memory** (`memory/cameras.md`) to use as it sees fit. The
  operator lists no cameras. Setup is a skill, `camera-setup`, that curunir
  invokes when it judges it needs to; nothing runs it automatically for now.
  The USB camera from the proof of concept becomes one found camera among
  several.
- **Agent surface:** replace `bash` + `snapshot.py` with an opt-in **`camera`
  tool** (`discover`, `preview`, `save`, `list`, `snapshot`, `describe`),
  unlocked by the `camera-setup` and `webcam` skills in the same way
  `to_audio` and `portfolio` are unlocked. The snapshot comes back as an
  attachment directly, so no LLM-driven `attach` step can be forgotten.
- **Watching:** add an in-process **`camera_watcher`** coroutine in `run.py`. It
  does cheap frame-diff motion detection with no LLM in the loop, and it wakes
  the agent only on change, with a cooldown. This replaces the pattern where
  every cron tick is a full agent turn plus a vision call.
- **UI:** add a read-only `camera` panel in the local console showing the
  cameras in memory, the last snapshot, a capture button, the capture log and
  the watch rules. Setup is not done here. It is gated by `WEBCAM_ENABLED`
  **and** by the persona allowlisting `webcam`. The panel is not exposed
  through the portal in the MVP.
- **Privacy:** the feature is opt-in per deployment, and cameras the user
  ruled out are refused in code. A capture ledger in SQLite
  records every capture. Frames are retained for a set period (default 7 days),
  and descriptions default to not identifying people. Raw frames never cross
  the portal relay unless `WEBCAM_PORTAL_IMAGES` is set.

## Where we are (verified in code, 2026-09-27)

| Piece | Today | Gap |
|---|---|---|
| Capture | `skills/webcam/snapshot.py`: ffmpeg grabs `warmup+1` frames with `-update 1` and keeps the last one | Every call pays a cold device open plus 10 warmup frames (~1–2 s). There is one device and no persistent stream. |
| Describe | `src.llm.describe_image(model, path, mime, prompt)` | **Its cache is keyed on image bytes only**, so asking a different question about the same frame returns the cached first answer. It also always appends the fixed prompt "Describe this image in detail…", which pulls answers toward a full scene inventory even when the question is narrow (bad for privacy and cost). |
| Invocation | The agent runs `python skills/webcam/snapshot.py` through the `bash` tool | Nothing enforces allowlisting or auditing. The agent has to remember to call `attach`. |
| Provisioning | Manual `docker-compose.webcam.yml` (`devices:` + `group_add: video`), one device named in `WEBCAM_DEVICE` | The operator has to know the device path or stream URL in advance. Curunir never looks for cameras, so network cameras it could reach go unused. Getting the GID right is manual. When the camera is absent, the first failure only shows up at call time. |
| Scheduled watching | Cron → `agent.handle(system_task_prompt=…)` under `sched:<id>:<ts>` | **The return value of a scheduled turn is discarded** (`scheduler._run_task`), and `attach` in a `sched:` session reaches nobody. The only way an alert gets out is if the agent chooses to run `email-send`. Every tick is also a full agent turn plus a vision call, even when nothing changed. |
| UI | Snapshots show up in the local console's Files rail because they land in `context/workspace/generated/` | There is no camera panel, and nothing tells the operator whether a camera is configured. |

## 1. Provisioning: a setup conversation

The proof of concept used one USB camera that the operator wired by hand. The
first-class feature turns that around. Curunir is equipped to find every
camera on the private network its host computer is on and every camera
plugged into the host's USB ports. It asks the user which of them are off
limits, and it saves the rest in its memory to use as it sees fit.

```
curunir decides it needs cameras  ->  search  ->  ask the user which are off limits
                                  ->  save the rest in memory  ->  use as it sees fit
```

All of this happens in conversation. There is no setup screen, no list of
cameras in `.env`, and no approval button.

**Configuration.** The operator turns the feature on for the deployment. The
rest is the conversation.

```
WEBCAM_ENABLED=true                  # master switch; default false
WEBCAM_SCAN_SUBNETS=192.168.1.0/24   # optional; see "Network reach" below
WEBCAM_DEVICE=/dev/video0            # back-compat only, see "Migration"
```

### 1.1 When setup runs

Setup is a skill, **`camera-setup`**, and it runs when curunir decides to
invoke it. Nothing triggers it automatically for now: there is no scan at
boot, no timer and no console button.

The skill's description tells curunir when setup is called for:
- it wants to look at something and its memory holds no cameras;
- the user mentions cameras, a new camera, or a camera that moved;
- a camera in memory has stopped answering;
- the user asks it to set up, find or forget cameras.

Because setup is a conversation, it runs only in a session where a user is
present (WS, local console chat, portal). In an email, scheduled, `ask:*` or
sub-agent session the tool refuses the setup actions, and curunir brings it
up the next time it talks with the user.

### 1.2 Search (`src/camera/discovery.py`)

Discovery runs a set of independent finders and merges their results into
candidates. Each finder has a timeout, and a finder that fails reports the
reason instead of failing the scan.

| Finder | Finds | How | Deps |
|---|---|---|---|
| **V4L2** | USB and built-in cameras on the host | Enumerate `/sys/class/video4linux/*` (`name`, `index`), keep nodes that offer a capture format (`v4l2-ctl --list-formats`, or an `ffprobe` open), and resolve the stable `/dev/v4l/by-id/*` link. A UVC camera exposes a second metadata node, which is dropped. | none (ffmpeg is in the image) |
| **Subnet sweep** | Every camera on the host's private network, including ones that announce nothing | For each address in the scan scope, try a TCP connect to 554 and 8554 (RTSP) and 80, 8000, 8080 (ONVIF/HTTP). On an open RTSP port send `OPTIONS`; on an open HTTP port send a unicast ONVIF `GetDeviceInformation` for vendor and model. Bounded concurrency and a short timeout per host. | none |
| **ONVIF WS-Discovery** | Most IP cameras and NVRs, with names | Send a WS-Discovery `Probe` for `NetworkVideoTransmitter` to `239.255.255.250:3702` and collect `ProbeMatch` replies (endpoint UUID, service address, scopes with vendor/model/name). | none (UDP socket + XML from stdlib) |
| **mDNS / SSDP** | Cameras that advertise `_rtsp._tcp`, `_onvif._tcp`, `_axis-video._tcp`, or a UPnP media device | One query on each protocol. | none for SSDP; mDNS needs a small stdlib query or `zeroconf` (to be decided in the issue) |
| **Host AVFoundation** (later) | Cameras on a Mac when curunir runs outside Docker | `ffmpeg -f avfoundation -list_devices true` | none |

A candidate is `(kind, stable_id, address, vendor, model, advertised_name,
needs_credentials, found_by)`. The **stable id** is what makes a camera the
same camera on the next search: the `by-id` path for USB (it survives
`/dev/videoN` renumbering), the ONVIF endpoint UUID for ONVIF cameras, and
host plus port for a bare RTSP endpoint. When a network camera in memory
stops answering, curunir searches again and follows the stable id to its new
address, so a DHCP lease change does not lose the camera.

### 1.3 Ask the user which cameras are off limits

The default is that curunir may use what it finds. The user's part is to
rule cameras out.

1. Curunir tells the user what it found: advertised name, vendor/model,
   address and how each was found.
2. It asks whether any of them are off limits. The user can rule cameras out
   from the list alone ("not the one in the bedroom").
3. For the rest, curunir takes **one preview frame** each and shows it, so
   the user can tell "front door" from "garage" without knowing IP
   addresses, and can still rule a camera out after seeing it. The preview
   goes to the user only, not to the vision model. It is written to the
   capture ledger with trigger `setup`, and the frame of a camera the user
   then rules out is deleted at once.
4. Curunir and the user agree on a name and a short description of what each
   camera looks at ("desk: home office, facing the door").
5. For a camera that needs a login, curunir asks the user for it in the
   conversation (see 1.4).
6. Curunir reads back what it is about to save and saves it.

Cameras the user ruled out are saved too, marked off limits. That is how the
next search knows not to ask about them again, and how the tool knows to
refuse them.

### 1.4 Save to memory

**Where.** Cameras are saved in curunir's memory as `memory/cameras.md` in
the agent's private context directory, and `memory/README.md` routes to it.
It is a plain markdown file like the other topical memory files, so the user
can read and edit it, and curunir can read it like any other memory.

```markdown
# Cameras

Network scanned: 192.168.1.0/24 (last search 2026-09-27)

| name  | status     | kind | looks at                     | source                       | stable id           |
|-------|------------|------|------------------------------|------------------------------|---------------------|
| desk  | usable     | v4l2 | home office, facing the door | /dev/v4l/by-id/usb-Logi...   | usb-Logi_C920_8A3F  |
| drive | usable     | rtsp | driveway and front gate      | rtsp://192.168.1.20/stream1  | onvif:urn:uuid:4f1c |
| -     | off limits | rtsp | bedroom (user ruled out)     | rtsp://192.168.1.31/stream1  | onvif:urn:uuid:9b20 |
```

The file is written by the `camera` tool's `save` action, not free-hand by
the model, so the table always parses. The tool reads it on every call:
`usable` cameras can be captured, `off limits` cameras are refused in code,
and an address that is not in the file is refused. Notes below the table are
free text for curunir and the user.

**Stream setup.** For an ONVIF camera the engine asks the camera for its
stream address (`GetProfiles`, `GetStreamUri`) and picks the main profile.
For a bare RTSP endpoint it tries the vendor's known paths (for example
`/stream1`, `/Streaming/Channels/101`, `/h264Preview_01_main`,
`/cam/realmonitor?channel=1&subtype=0`) and keeps the first that `ffprobe`
accepts. If none works, it asks the user for the path.

**Camera logins.** Most network cameras need a username and password, and
the user gives them in the conversation.
- They are stored in `camera-credentials.json` (mode 0600) in the agent's
  private context directory, keyed by stable id. They are never written to
  `memory/cameras.md`.
- The tool never returns them to the model, and sources are redacted
  wherever they are logged or displayed (`rtsp://***@192.168.1.20/stream1`).
- A password typed in chat is in the conversation transcript. To limit
  that, the `password` argument of the tool call is redacted before the
  conversation is persisted, and the memory extractor is told not to record
  credentials. The user's own message still holds it, so the skill advises
  a camera-only, view-only account (see open question 5).
- Curunir **never tries default or guessed passwords**. A camera without a
  login stays in the file as "needs login" until the user gives one.

**Health.** There is no boot check. A camera is checked when it is used and
when setup runs. USB: the node exists, `os.access(R_OK)` passes (this
catches the GID problem with a precise message), and a format probe
succeeds. Network: an `ffprobe` with a 5 s timeout. The tool returns the
precise error, and the skill tells curunir to run setup again when a camera
has gone missing.

**Multiple cameras.** Every tool call takes `camera=<name>`. Curunir picks
the camera from the "looks at" descriptions in its memory. A missing name
works only when exactly one camera is usable; otherwise the tool returns the
list of names.

### 1.5 What curunir can reach from inside Docker

Discovery can only find what the container can see, and the default compose
setup hides both kinds of camera. The plan changes the webcam override so
that discovery works, and reports clearly when it cannot.

**USB devices.** Today the override maps one named device. That cannot
support discovery, and compose refuses to start when the named device is
missing. Finding the cameras on the host's USB ports requires granting video
devices as a class:

```yaml
# docker-compose.webcam.yml
services:
  curunir:
    device_cgroup_rules:
      - "c 81:* rmw"          # V4L2 character devices
    volumes:
      - /dev:/dev/host:ro     # device nodes, including cameras plugged in later
      - /run/udev:/run/udev:ro
    group_add:
      - "${WEBCAM_GID:-video}"
```

This covers video devices only, not disks, microphones or other USB devices.
It also fixes hotplug and the missing-device startup failure. The exact
mount (all of `/dev` read-only under a prefix, or only `/dev/v4l` and the
video nodes) is settled in the issue, together with whether `/sys` shows the
host's devices on the target kernels.

`scripts/webcam-setup.sh` stays as a one-time host helper. It reads the video
GID (`stat -c %g`) and writes `WEBCAM_GID`, and it writes
`WEBCAM_SCAN_SUBNETS` from the host's LAN interfaces.

**Network reach.** Camera boxes stay on Docker's default bridge network
(owner decision: keep the safe setting). On it the container can open
connections to addresses on the host's network, but multicast does not cross
the bridge and the container cannot see which network the host is on. So:

| Setup | Multicast finders (ONVIF, mDNS, SSDP) | Subnet sweep |
|---|---|---|
| Docker, bridge network (default, and the plan's choice) | do not work | works, once the network range is known |
| Run on the host (`python run.py`) | work | works, range read from interfaces |
| Docker Desktop on macOS | do not work | works; USB cameras are not available at all |

The sweep is the finder that reaches every camera on the host's network, and
it is the first network finder to build. The multicast finders add names and
models when curunir runs on the host.

**Learning the network range.** The sweep needs the host's network range.
Curunir gets it, in order, from:
1. `WEBCAM_SCAN_SUBNETS`, if the host helper wrote it;
2. the "Network scanned" line in `memory/cameras.md` from an earlier setup;
3. a guess: it tries the usual home-router addresses (`192.168.0.1`,
   `192.168.1.1`, `10.0.0.1` and similar) and takes the /24 of the one that
   answers;
4. the conversation: it asks the user for the router's address or for the
   address of any one camera.

**Scan limits.** Discovery is an active network scan, so it is bounded:
- It scans only private ranges (RFC 1918, link-local, IPv6 ULA). A public
  range is refused.
- A range larger than /22 is refused unless `WEBCAM_SCAN_MAX_HOSTS` is
  raised.
- It sends at most the probes listed in 1.2, with no login attempts.
- It runs only inside the setup conversation (1.1).

### 1.6 Migration

A deployment that sets `WEBCAM_DEVICE` today keeps working. When
`memory/cameras.md` does not exist, the tool treats `WEBCAM_DEVICE` as one
usable camera named `default`. The first setup conversation writes it into
memory with the others.

## 2. Agent surface

Add an opt-in **`camera`** tool (`src/tools/camera_tool.py`), registered in
`_OPT_IN_SCHEMAS` and listed in the `tools:` frontmatter of both camera
skills.
Like `to_audio`, it is in `_ASYNC_EXECUTORS_WITH_ATTACHMENTS`, so the captured
frame is attached by the tool itself.

```
# setup (camera-setup skill; only in a session with a user present)
camera(action="discover")                       -> candidates + why any finder was skipped
camera(action="preview", candidate="c3")        -> one frame, attached for the user
camera(action="save", cameras=[{candidate, name, looks_at, status}])
                                                -> writes memory/cameras.md
camera(action="set_login", camera="drive", username=..., password=...)
camera(action="forget", camera="desk")

# use (webcam skill)
camera(action="list")                                  -> cameras in memory + status
camera(action="snapshot", camera="desk", attach=true)  -> path (+ attachment)
camera(action="describe", camera="desk", question="Is the door closed?")
                                                       -> {path, description, model}
```

**Two skills.**
- **`camera-setup`** (new) carries the setup conversation of §1: search, ask
  which cameras are off limits, preview and name the rest, ask for logins,
  save to memory. Its description states when curunir should reach for it
  (§1.1), so the decision to run setup is curunir's.
- **`webcam`** (existing, rewritten) is for using the cameras. It tells
  curunir to read `memory/cameras.md`, pick the camera whose "looks at" fits
  the question, and load `camera-setup` when memory has no camera that fits
  or a camera has gone missing.

**Use as it sees fit.** Today's skill says to capture only when asked. That
rule goes: curunir may capture from any usable camera whenever it judges
that a look helps the task at hand. The limits are the ones enforced in
code: off-limits cameras are refused, every capture is in the ledger, and
email-originated turns cannot capture (§5).

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
2. by runtime: the tab is present only if `WEBCAM_ENABLED` is set and
   `memory/cameras.md` holds at least one usable camera.
   `enabled_modules()` today only takes the allowlist, so extend it with an
   optional `runtime_available: set[str]` (module names whose backing
   hardware/config exists) rather than special-casing camera in the channel.

The panel shows what the setup conversation produced. It does not search for
cameras or change which are off limits; that stays in the conversation.

Contents:
- One card per usable camera showing its name and "looks at" text, status
  (ok / error text), the last snapshot thumbnail with its time, and a
  **Capture now** button (`POST /api/camera/<name>/snapshot`, token-gated
  like the schedule writes). Off-limits cameras are listed by address, with
  no controls.
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
- **Off limits is enforced in code.** Curunir may use what it finds unless
  the user ruled it out. A camera marked off limits in `memory/cameras.md`,
  and any address that is not in the file, is refused by the tool for
  captures, watch rules and live preview, whatever the model asks for. The
  user can also mark a camera off limits later, in conversation or by
  editing the file.
- **Setup preview.** Setup takes one frame per found camera so the user can
  recognize it. It goes to the user and not to the vision model, and the
  frame of a camera the user then rules out is deleted at once (§1.3).
- **Discovery is bounded** to private networks, sends no login attempts, and
  runs only inside a setup conversation with a user present (§1.1, §1.5).
  Camera logins are stored 0600 outside memory and never returned to the
  model; the limits of giving a password in chat are in §1.4.
- **Who can trigger a capture:** curunir in a session with a user on an
  allowed channel, schedules and watch rules, and the local UI button. **Email-originated
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

The voice plan is [`docs/voice-mode-plan.md`](voice-mode-plan.md) (PR #554). Its design fits this one: curunir stays the only brain, and voice reaches the camera through the `camera` tool with no voice-specific path.

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
| **1a — MVP: setup conversation, USB discovery, tool** | `src/camera/` (discovery, capture moved out of the skill, reader/writer for `memory/cameras.md`), V4L2 finder, new `camera-setup` skill (search → off limits → preview and name → save), opt-in `camera` tool (setup and use actions) with the off-limits check and the user-present guard, `webcam` skill rewritten to use the tool and memory, `WEBCAM_ENABLED`, `WEBCAM_DEVICE` fallback as camera `default`, capture ledger, class-wide device grant in the override + `scripts/webcam-setup.sh`, email-trigger block, retention sweep | `WEBCAM_ENABLED`, `WEBCAM_GID`, `WEBCAM_RETENTION_DAYS`, `WEBCAM_ALLOW_EMAIL_TRIGGER`; no new Python deps | 4–5 (memory file + engine · V4L2 finder · tool + two skills · ledger+retention · compose/setup script) |
| **1b — network discovery** | Subnet sweep finder with scan limits, learning the network range (env → memory → guess → ask), ONVIF stream lookup and RTSP path table, camera logins given in conversation (credential file, redaction of the tool-call argument), re-locate by stable id; multicast finders (ONVIF WS-Discovery, mDNS/SSDP) last, since they only work when curunir runs on the host | `WEBCAM_SCAN_SUBNETS`, `WEBCAM_SCAN_MAX_HOSTS`; aim for no new Python deps (stdlib sockets + XML), `zeroconf` only if mDNS needs it | 3–4 (sweep + range + limits · stream lookup · logins · multicast finders) |
| **2 — UI panel + notify** | `camera` module in `src/modules.py` with runtime gating, read-only local-console tab (cameras in memory, status, last snapshot, capture button, ledger), `notify()` sink primitive (email/local), sensitive-attachment handling in the portal | `WEBCAM_PORTAL_IMAGES` | 3 (module gating · panel · notify primitive) |
| **3 — watching** | Persistent frame source + shared buffer, numpy motion detection, `camera.db` watch rules + engine, watcher coroutine with notify/describe/agent modes, cooldown + daily vision budget, rules editable in UI + tool, MJPEG live preview | `WEBCAM_VISION_DAILY_BUDGET`, `WEBCAM_WATCH_FPS`; numpy made explicit in `requirements.txt` | 4 (frame source/buffer · detector · rules store+tool · watcher+UI) |
| **4 — later / optional** | Clips (`action="clip"`), per-camera resolution/ROI editor in UI, portal last-snapshot (opt-in), realtime-voice frame push (with #551), automatic setup triggers (search at boot or on a timer, `WEBCAM_RESCAN_HOURS`), AVFoundation finder for Mac hosts, pan/tilt through ONVIF | TBD | as needed |

**Tests.** Everything is testable without hardware in the same style as today's
`snapshot.py` tests: an injected ffmpeg runner, a fake frame source that yields
synthetic numpy frames for the detector, and a fake describer. Finders take an
injected socket/sysfs layer, so discovery tests replay recorded ONVIF
`ProbeMatch` and RTSP `OPTIONS` replies and a fake `/sys/class/video4linux`
tree. The guards get their own tests: setup actions are refused in email and
`sched:*` sessions, and captures are refused for off-limits cameras and for
addresses that are not in memory. There is one
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
5. **Camera passwords in chat.** Setup is conversational, so the user types
   a camera's password into the conversation. The plan keeps it out of
   memory and redacts it from the saved tool call, but the user's own message
   still holds it in the saved conversation. Is that acceptable if the skill
   advises a view-only camera account, or should the user's message be
   scrubbed from the saved conversation as well?
6. **Should "as it sees fit" reach scheduled and watcher turns** with no
   user present, or only sessions where curunir is talking with someone?
   (The plan allows schedules and watch rules, and blocks email.)

## Decisions (owner, 2026-09-27)

- **Opt-out, not approval.** Curunir may use every camera it finds except the
  ones the user says are off limits (§1.3).
- **Setup is conversational.** No setup screen or buttons; the local console
  panel is read-only (§4).
- **Cameras are saved in curunir's memory** (`memory/cameras.md`), and
  curunir uses them as it sees fit (§1.4, §2).
- **Setup is not triggered automatically for now.** It runs when curunir
  decides to invoke the `camera-setup` skill (§1.1). Automatic triggers are
  a later option.
- **Host USB cameras are in scope**, which requires the class-wide video
  device grant in the compose override (§1.5).
- **Camera boxes stay on Docker's bridge network** (the safe setting). No
  `network_mode: host`. The subnet sweep is what finds the cameras on the
  host's network; names and models may be missing for some, and the preview
  frame is how the user tells them apart.
- **The setup preview frame is allowed**, one frame per found camera, shown
  to the user only and recorded in the ledger (§1.3).
