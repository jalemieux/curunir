# Webcam as a first-class container feature — build plan

Plan for [#552](https://github.com/jalemieux/curunir/issues/552). This is planning
only: no code ships with this doc. Each phase below is sized to become one or
more implementation issues.

## TL;DR

- **Provisioning is discovery, not declaration.** The operator sets one flag,
  `WEBCAM_ENABLED`, and does not list cameras. Curunir **searches** for cameras
  it can reach (USB/V4L2 on the box, ONVIF and RTSP cameras on the local
  network), **shows the user what it found** with a preview frame each, and
  uses only the cameras **the user approves**. Approved cameras are stored in
  a registry (`camera.db`) with a name the user picks. Nothing is captured
  from a camera the user has not approved, apart from the one setup preview.
  The USB camera from the proof of concept becomes one discovered camera among
  several.
- **Agent surface:** replace `bash` + `snapshot.py` with an opt-in **`camera`
  tool** (`discover`, `approve`, `list`, `snapshot`, `describe`), unlocked by
  the `webcam` skill in the same way `to_audio` and `portfolio` are unlocked.
  The skill carries the setup conversation. The snapshot comes back as an
  attachment directly, so no LLM-driven `attach` step can be forgotten.
- **Watching:** add an in-process **`camera_watcher`** coroutine in `run.py`. It
  does cheap frame-diff motion detection with no LLM in the loop, and it wakes
  the agent only on change, with a cooldown. This replaces the pattern where
  every cron tick is a full agent turn plus a vision call.
- **UI:** add a `camera` panel in the local console showing found cameras with
  Approve / Ignore buttons, the last snapshot, a capture button, the capture
  log and the watch rules. It is gated by `WEBCAM_ENABLED` **and** by the
  persona allowlisting `webcam`. The panel is not exposed through the portal
  in the MVP.
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
| Provisioning | Manual `docker-compose.webcam.yml` (`devices:` + `group_add: video`), one device named in `WEBCAM_DEVICE` | The operator has to know the device path or stream URL in advance. Curunir never looks for cameras, so network cameras it could reach go unused. Getting the GID right is manual. When the camera is absent, the first failure only shows up at call time. |
| Scheduled watching | Cron → `agent.handle(system_task_prompt=…)` under `sched:<id>:<ts>` | **The return value of a scheduled turn is discarded** (`scheduler._run_task`), and `attach` in a `sched:` session reaches nobody. The only way an alert gets out is if the agent chooses to run `email-send`. Every tick is also a full agent turn plus a vision call, even when nothing changed. |
| UI | Snapshots show up in the local console's Files rail because they land in `context/workspace/generated/` | There is no camera panel, and nothing tells the operator whether a camera is configured. |

## 1. Provisioning: discover, confirm, configure

The proof of concept used one USB camera that the operator wired by hand. The
first-class feature turns that around: the operator enables the feature, and
curunir finds the cameras. The flow has four steps.

```
search  ->  show the user what was found  ->  user approves  ->  configured and usable
```

**Configuration.** The operator sets a switch and, at most, a scan scope.
Cameras are not listed in `.env`.

```
WEBCAM_ENABLED=true                  # master switch; default false
WEBCAM_SCAN_SUBNETS=192.168.1.0/24   # optional; see "Network reach" below
WEBCAM_DEVICE=/dev/video0            # back-compat only, see "Migration"
```

### 1.1 Search (`src/camera/discovery.py`)

Discovery runs a set of independent finders and merges their results into
candidates. Each finder has a timeout, and a finder that fails reports the
reason instead of failing the scan.

| Finder | Finds | How | Deps |
|---|---|---|---|
| **V4L2** | USB and built-in cameras on the box | Enumerate `/sys/class/video4linux/*` (`name`, `index`), keep nodes that offer a capture format (`v4l2-ctl --list-formats`, or an `ffprobe` open), and resolve the stable `/dev/v4l/by-id/*` link. A UVC camera exposes a second metadata node, which is dropped. | none (ffmpeg is in the image) |
| **ONVIF WS-Discovery** | Most IP cameras and NVRs | Send a WS-Discovery `Probe` for `NetworkVideoTransmitter` to `239.255.255.250:3702` and collect `ProbeMatch` replies (endpoint UUID, service address, scopes with vendor/model/name). | none (UDP socket + XML from stdlib) |
| **mDNS / SSDP** | Cameras that advertise `_rtsp._tcp`, `_onvif._tcp`, `_axis-video._tcp`, or a UPnP media device | One query on each protocol. | none for SSDP; mDNS needs a small stdlib query or `zeroconf` (to be decided in the issue) |
| **Subnet sweep** | Cameras that announce nothing, and every camera when multicast is unavailable | For each address in the scan scope, try a TCP connect to 554 and 8554 (RTSP) and 80, 8000, 8080 (ONVIF/HTTP). On an open RTSP port send `OPTIONS`; on an open HTTP port send a unicast ONVIF `GetDeviceInformation`. Bounded concurrency and a short timeout per host. | none |
| **Host AVFoundation** (later) | Cameras on a Mac when curunir runs outside Docker | `ffmpeg -f avfoundation -list_devices true` | none |

A candidate is `(kind, stable_id, address, vendor, model, advertised_name,
needs_credentials, found_by)`. The **stable id** is what makes a camera the
same camera on the next scan: the `by-id` path for USB (it survives
`/dev/videoN` renumbering), the ONVIF endpoint UUID for ONVIF cameras, and
host plus port for a bare RTSP endpoint. When an approved network camera
stops answering, the registry re-runs discovery and follows the stable id to
its new address, so a DHCP lease change does not break the camera.

**When the search runs.**
- On demand, when the user asks ("find my cameras", "set up the webcam"), from
  the `camera` tool or the local console's **Scan** button.
- Once at boot when `WEBCAM_ENABLED` is set. The boot scan only records
  candidates and checks approved cameras. It captures nothing and approves
  nothing. It never blocks startup.
- Not on a timer by default. A periodic rescan is a later option
  (`WEBCAM_RESCAN_HOURS`).

When a scan finds a camera the user has not seen before, curunir says so the
next time the user is in an interactive session, and the local console shows
a badge on the Camera tab. It does not message the user unprompted.

### 1.2 Confirm with the user

The user decides which cameras curunir may use. This step cannot be skipped
and the model cannot answer it for the user.

1. Curunir lists the candidates: advertised name, vendor/model, address and
   how it was found.
2. For each candidate it can open, curunir takes **one preview frame** and
   shows it to the user, so the user can tell "front door" from "garage"
   without knowing IP addresses. The preview is shown to the user only. It is
   not sent to the vision model, and it is written to the capture ledger with
   trigger `setup`.
3. The user picks: **approve** (and give it a name, such as `desk`),
   **ignore** (keep it out of future prompts), or leave it for later.
4. Cameras that need a login stay as "found, needs credentials" until the
   user supplies them (see 1.3).

The same decision is available in two places: in chat through the `webcam`
skill, and in the local console's Camera tab with Approve / Ignore buttons.
The console path has no LLM in it.

**Guard on approval.** Approval is an `approve` action on the `camera` tool,
so it must not be reachable by prompt injection. The engine accepts it only
when all of these hold:
- the session is interactive (WS, local console or portal). Email, `sched:*`,
  `ask:*` and delegate sub-agent sessions are refused;
- the candidate id came from a scan, so the model cannot approve an address
  it made up or read in a web page;
- the address is on a local network (see 1.4).

Every approval records who approved it (session and channel) and when.

### 1.3 Configure

**Registry.** Approved cameras live in a `cameras` table in `camera.db`, next
to the capture ledger and watch rules, with the same `db.py` / `engine.py`
split as `schedule_store`.

```
cameras: id, name (unique, user-chosen), kind (v4l2|onvif|rtsp|http),
         stable_id, source, vendor, model, resolution,
         state (found|approved|ignored|disabled),
         approved_by, approved_at, last_seen, last_ok, last_error
```

Cameras are hardware of the container, not of one agent. In a multi-agent
container the registry sits in the shared directory, and which agents may use
the cameras follows each persona's skill allowlist.

**Stream setup.** For an ONVIF camera the engine asks the camera for its
stream address (`GetProfiles`, `GetStreamUri`) and picks the main profile.
For a bare RTSP endpoint it tries the vendor's known paths (for example
`/stream1`, `/Streaming/Channels/101`, `/h264Preview_01_main`,
`/cam/realmonitor?channel=1&subtype=0`) and keeps the first that `ffprobe`
accepts. If none works, it asks the user for the path.

**Credentials.** Most network cameras need a username and password.
- The user enters them in the **local console** (a form on the camera card).
  This is the recommended path, because a password typed into chat enters
  conversation history and memory extraction.
- They are stored in `camera-credentials.json` (mode 0600) in the shared
  directory, keyed by camera id. They are not stored in `camera.db`, which
  the read-only `query`-style paths and the UI read.
- The tool never returns them to the model. Sources are redacted everywhere
  they are logged or displayed (`rtsp://***@192.168.1.20/stream1`).
- Curunir **never tries default or guessed passwords**. A camera without
  supplied credentials stays "needs credentials".

**Health.** Each approved camera is checked at boot and on demand (tool
`list` with `refresh=true`, or the UI ⟳ button). USB: the node exists,
`os.access(R_OK)` passes (this catches the GID problem with a precise
message), and a format probe succeeds. Network: an `ffprobe` with a 5 s
timeout. A failed camera is logged at WARNING and marked with its error; the
tool and UI report the stored error instead of calling ffmpeg again. This
fits the existing soft startup-warning pattern for missing API keys.

**Multiple cameras.** Every tool call and UI element takes `camera=<name>`.
A missing name works only when exactly one camera is approved; otherwise the
tool returns the list of names and asks which.

### 1.4 What curunir can reach from inside Docker

Discovery can only find what the container can see, and the default compose
setup hides both kinds of camera. The plan changes the webcam override so
that discovery works, and reports clearly when it cannot.

**USB devices.** Today the override maps one named device. That cannot
support discovery, and compose refuses to start when the named device is
missing. The override changes to grant video devices as a class:

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

This also fixes hotplug and the missing-device startup failure. It grants the
container every video device on the host, which is the point of discovery;
the per-camera control moves from compose to the user's approval. The exact
mount (all of `/dev` read-only under a prefix, or only `/dev/v4l` and the
video nodes) is settled in the issue, together with whether `/sys` shows the
host's devices on the target kernels.

`scripts/webcam-setup.sh` stays as the host helper. It reads the video GID
(`stat -c %g`) and writes `WEBCAM_GID`, and it now also writes
`WEBCAM_SCAN_SUBNETS` from the host's LAN interfaces.

**Network reach.** On Docker's default bridge network the container can open
connections to LAN addresses, but multicast does not cross the bridge and the
container cannot see which subnet the host is on. So:

| Setup | Multicast finders (ONVIF, mDNS, SSDP) | Subnet sweep |
|---|---|---|
| Docker, bridge network (default) | do not work | works, using `WEBCAM_SCAN_SUBNETS` |
| Docker, `network_mode: host` (Linux) | work | works, subnets read from interfaces |
| Run on the host (`python run.py`) | work | works |
| Docker Desktop on macOS | do not work | works; USB cameras are not available at all |

The sweep is therefore the finder that always works, and the multicast
finders add names and models when they are available. When the container is
on a bridge network and `WEBCAM_SCAN_SUBNETS` is unset, discovery reports
"USB only: set `WEBCAM_SCAN_SUBNETS` or run `scripts/webcam-setup.sh` to scan
the network" instead of returning an empty list with no reason.

**Scan limits.** Discovery is an active network scan, so it is bounded:
- It scans only private ranges (RFC 1918, link-local, IPv6 ULA). A public
  range in `WEBCAM_SCAN_SUBNETS` is rejected at boot.
- A subnet larger than /22 is refused unless `WEBCAM_SCAN_MAX_HOSTS` is
  raised.
- It sends at most the probes listed in 1.1, with no login attempts.
- Only an interactive user or the console button can start a scan. Email
  turns and scheduled turns cannot.

### 1.5 Migration

A deployment that sets `WEBCAM_DEVICE` today keeps working. At first boot the
registry imports it as an approved camera named `default`, with
`approved_by = env`, because the operator who wrote it into `.env` already
made that choice. The proof-of-concept box keeps its camera with no setup
conversation, and further cameras arrive through discovery.

## 2. Agent surface

Add an opt-in **`camera`** tool (`src/tools/camera_tool.py`), registered in
`_OPT_IN_SCHEMAS` and listed in the `webcam` skill's `tools:` frontmatter.
Like `to_audio`, it is in `_ASYNC_EXECUTORS_WITH_ATTACHMENTS`, so the captured
frame is attached by the tool itself.

```
# setup
camera(action="discover")                              -> candidates + why any finder was skipped
camera(action="preview", candidate="c3")               -> one frame, attached for the user
camera(action="approve", candidate="c3", name="desk")  -> camera (guarded, see §1.2)
camera(action="ignore", candidate="c4")
camera(action="rename" | "disable" | "remove", camera="desk")

# use
camera(action="list")                                  -> approved cameras + status
camera(action="snapshot", camera="desk", attach=true)  -> path (+ attachment)
camera(action="describe", camera="desk", question="Is the door closed?")
                                                       -> {path, description, model}
```

**The `webcam` skill carries the setup conversation.** Its text tells the
agent to run `discover` when the user asks for a camera and none is approved,
to show each candidate with its preview, to ask the user which to enable and
what to call them, and to send the user to the local console for camera
passwords. `snapshot` and `describe` only accept approved cameras; on an
unapproved or unknown name the tool returns the setup hint instead of
capturing.

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
2. by runtime: the tab is present only if `WEBCAM_ENABLED` is set. It shows
   even with no camera approved, because setup starts here.
   `enabled_modules()` today only takes the allowlist, so extend it with an
   optional `runtime_available: set[str]` (module names whose backing
   hardware/config exists) rather than special-casing camera in the channel.

Contents:
- **Found cameras:** a **Scan** button (`POST /api/camera/discover`) and one
  row per candidate with its preview frame, vendor/model and address, and
  **Approve** (with a name field) / **Ignore** buttons. A candidate that
  needs a login shows a username/password form. With no cameras approved,
  this section is the whole tab.
- One card per approved camera showing status (ok / error text), the last
  snapshot thumbnail with its time, a **Capture now** button
  (`POST /api/camera/<name>/snapshot`, token-gated like the schedule writes),
  and Rename / Disable / Remove.
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
- **Approved cameras only.** Finding a camera gives curunir no right to use
  it. Captures, watch rules and live preview work only on cameras the user
  approved (§1.2). The one exception is the single setup preview frame, which
  goes to the user and not to the vision model.
- **Discovery is bounded** to local networks, sends no login attempts, and
  can only be started by an interactive user or the console (§1.4). Camera
  passwords are entered in the console, stored 0600 outside `camera.db`, and
  never returned to the model (§1.3).
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
| **1a — MVP: registry, USB discovery, tool** | `src/camera/` (`camera.db` registry + engine, capture moved out of the skill), V4L2 finder, the discover → preview → approve flow with the approval guard, opt-in `camera` tool (setup and use actions), `WEBCAM_ENABLED`, import of `WEBCAM_DEVICE` as camera `default`, capture ledger, class-wide device grant in the override + `scripts/webcam-setup.sh`, skill rewritten to use the tool and carry the setup conversation, email-trigger block, retention sweep | `WEBCAM_ENABLED`, `WEBCAM_GID`, `WEBCAM_RETENTION_DAYS`, `WEBCAM_ALLOW_EMAIL_TRIGGER`; no new Python deps | 4–5 (registry/engine · V4L2 finder + approval flow · tool + skill · ledger+retention · compose/setup script) |
| **1b — network discovery** | Subnet sweep finder with scan limits, ONVIF WS-Discovery finder, ONVIF stream lookup and RTSP path table, credential store, re-locate by stable id, clear "cannot scan" reporting on bridge networks; mDNS/SSDP finders last | `WEBCAM_SCAN_SUBNETS`, `WEBCAM_SCAN_MAX_HOSTS`; aim for no new Python deps (stdlib sockets + XML), `zeroconf` only if mDNS needs it | 3–4 (sweep + limits · ONVIF discovery + stream lookup · credentials · mDNS/SSDP) |
| **2 — UI panel + notify** | `camera` module in `src/modules.py` with runtime gating, local-console tab (found cameras with Scan / Approve / Ignore and the credential form, status, last snapshot, capture button, ledger), `notify()` sink primitive (email/local), sensitive-attachment handling in the portal | `WEBCAM_PORTAL_IMAGES` | 3 (module gating · panel · notify primitive) |
| **3 — watching** | Persistent frame source + shared buffer, numpy motion detection, `camera.db` watch rules + engine, watcher coroutine with notify/describe/agent modes, cooldown + daily vision budget, rules editable in UI + tool, MJPEG live preview | `WEBCAM_VISION_DAILY_BUDGET`, `WEBCAM_WATCH_FPS`; numpy made explicit in `requirements.txt` | 4 (frame source/buffer · detector · rules store+tool · watcher+UI) |
| **4 — later / optional** | Clips (`action="clip"`), per-camera resolution/ROI editor in UI, portal last-snapshot (opt-in), realtime-voice frame push (with #551), periodic rescan (`WEBCAM_RESCAN_HOURS`), AVFoundation finder for Mac hosts, pan/tilt through ONVIF | TBD | as needed |

Phase 1b depends on the credential form for cameras that need a login. Until
the Phase 2 panel ships, credentials for those cameras come from
`scripts/webcam-setup.sh`, which writes the same credential file.

**Tests.** Everything is testable without hardware in the same style as today's
`snapshot.py` tests: an injected ffmpeg runner, a fake frame source that yields
synthetic numpy frames for the detector, and a fake describer. Finders take an
injected socket/sysfs layer, so discovery tests replay recorded ONVIF
`ProbeMatch` and RTSP `OPTIONS` replies and a fake `/sys/class/video4linux`
tree. The approval guard gets its own tests (email, `sched:*` and made-up
candidate ids are refused). There is one
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
5. **Where may a camera be approved?** The plan allows approval in chat and
   in the local console. Console-only is stricter: no model is involved, at
   the cost of making the user open the console once per camera.
6. **Host networking for camera boxes?** `network_mode: host` gives the best
   discovery (names and models from ONVIF/mDNS) but removes Docker's network
   isolation for that instance. The plan keeps the bridge network and relies
   on the subnet sweep by default.
7. **Class-wide device grant.** Discovery of USB cameras needs the container
   to see every video device on the host. Is that acceptable on the camera
   box, given that use still requires approval per camera?
8. Should the **setup preview frame** be allowed before approval? Without it
   the user has to identify cameras by address and model alone.
