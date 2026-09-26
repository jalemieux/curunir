# Agents and Containers — Design

**Date:** 2026-09-26
**Status:** Revision 2. All three open questions decided 2026-09-26; ready
for phase 1.
**Related:** PR #546 (concept: `docs/agents-and-containers.md`),
`2026-05-29-persona-deployment-design.md`

Revision 2 checks every code reference against `main` at `34fa7a1` and
corrects the first draft where it was wrong: `build_static_prompt` lives in
`src/agent/system_prompt.py`, not `skills.py`; `load_skill` takes no config
today; 27 markdown files carry `context/` literals, not ~31; several path
literals were missed (email state, `.ws-token`, `_enrich_attachments`,
`readers.py`); the portal drops unknown frame types, so it is not fully
"no change"; and transcripts are persisted by `agent_worker`, not by
`Agent.handle`, which is what makes `ask_agent` unpersisted for free.

## Problem

`docs/agents-and-containers.md` defines three concepts (agent, container, and
inbound/outbound lists) and two collaboration modes: two-way *ask* inside a
container, and one-way *handoff* between containers. Curunir today has none of
these as first-class units. A deployment is one process running one persona
with one `context/`. This spec maps the concepts onto the existing
architecture with the smallest set of new seams. It keeps today's
single-persona deployment byte-for-byte unchanged.

## Mapping onto what exists

| Concept | Today | After |
|---|---|---|
| **Agent** | the single `Agent` + `AgentConfig` built in `run.py:main` | one `Agent` per manifest entry, each with its own `AgentConfig` (persona + context dir) |
| **Container** | one curunir process / Docker container | the same process. It now hosts N agents and owns the lists |
| **Lists** | implicit: user-only (channels reach the user, email is recipient-restricted) | explicit `inbound` / `outbound` in the container manifest. The default is `[user]`, which is exactly today |

The container remains the process. Nothing changes about how the process is
deployed. A container with one agent and `[user]` lists **is** today's
deployment, and that is the backward-compatibility path.

The existing seams already carry most of the load:

- `Agent` state is per instance (`sessions`, prompts, cancel events).
- Every tool receives `config` (`execute_tool_call`, `src/tools/dispatcher.py:45`).
- The memory extractor, conversation store, scheduler and skill allowlist all
  take `config` / `context_dir`.
- Transcripts are persisted by `agent_worker` (`run.py:426`, `run.py:498`),
  not by `Agent.handle`. A per-agent worker therefore persists per agent
  with no change to the store.

What is hard-wired to one agent is `run.py` wiring, about a dozen path
literals (listed under Context layout), and 27 markdown files under
`skills/` and `personas/` that spell `context/memory`,
`context/identity.md`, `context/workspace` and similar literally. 22 of the
27 enter prompts; the other five are persona READMEs and a template.

## Decisions

1. **Container manifest.** A new optional file, `container.yaml`, is selected
   with `CURUNIR_CONTAINER=<path>`. When it is absent, boot synthesizes a
   one-agent container from `CURUNIR_PERSONA` with `inbound: [user]`,
   `outbound: [user]` and agent context `.`, which is today's exact behavior.
   `CURUNIR_PERSONA` keeps working. The manifest is the multi-agent path.
2. **Agent = persona bundle + its own context dir.** Personas stay the unit of
   curation (skills, prompts, keys). An agent names a persona. `persona`
   defaults to the agent's name.
3. **Existing deployments do not migrate.** The legacy agent's context is the
   container root `context/` itself (`context: .`). New agents default to
   `context/agents/<name>/`.
4. **Paths in skills become two placeholders, `{{context}}` and `{{shared}}`.**
   One function renders them. With one legacy agent both render to `context`,
   so prompts are byte-identical and the prompt cache is unaffected.
5. **Messages carry an optional `agent` field.** Channels pass it through. A
   missing field means the default agent, so every existing client keeps
   working.
6. **Ask is a tool (`ask_agent`) shaped like `delegate`.** The sibling's config
   runs in a fresh, unpersisted session. The answer returns to the asker.
7. **Handoff is a tool (`handoff`) over a new peer HTTP channel.** The
   receiver's answer is delivered to *its* user channel. There is structurally
   no path back to the sender.
8. **The lists are enforced on both ends.** The sender checks its outbound
   list. The receiver checks its inbound list after authenticating the sender.
   A tool or listener that its lists cannot use is never registered.

## Container manifest

```yaml
# container.yaml
name: home
agents:
  - name: everyday
    persona: default
    context: .              # legacy agent: context/ itself
    default: true           # receives messages with no `agent` field, and email
  - name: finance           # persona: finance, context: agents/finance (defaults)
inbound: [user]             # who may send to this container
outbound: [user, vault]     # where this container's messages may go
peers:                      # addressing for every container named in a list
  vault:
    url: http://vault:8767
    token_env: PEER_VAULT_TOKEN   # the shared secret for this pair, from env
user_delivery: portal       # where handoff answers go: portal | local_web | email
```

`src/container.py` holds a frozen `ContainerManifest` dataclass plus
`load_container(path)` and `synthesize_container(persona_name)`. It mirrors
`src/persona.py` (`Persona` frozen dataclass; `load_persona` raises
`FileNotFoundError` on a missing file and `ValueError` on malformed YAML,
`src/persona.py:48-70`): it validates at boot and raises on a malformed
manifest. Validation rules:

- `name` is present. Agent names are unique.
- Exactly one agent is the default (implied when there is one agent).
- Every persona exists (`load_persona` is called for each; its errors
  propagate).
- `user` appears in both lists.
- Every container named in a list has a `peers` entry, and the env var its
  `token_env` names is set. A missing secret fails boot, not the first
  handoff.
- `user_delivery` names an enabled channel.

## Context layout

```
context/                         # container root = shared area
  workspace/generated/           # deliverables (shared)
  uploads/  cards/               # staged inputs (shared)
  profile.md                     # the user profile: one per container (moved from memory/)
  usage.db  email_state.json  .ws-token
  identity.md memory/ conversations/ schedules.db   # ← legacy agent (context: .)
  agents/
    finance/
      identity.md
      memory/            (incl. portfolio.db, crm.db; no profile.md)
      conversations/
      schedules.db
```

`AgentConfig` (`src/config.py:6-39`) gains `agent_name` and `shared_dir`.
Today every path field is an independent literal (`identity_file`,
`usage_db`, `schedules_db`, `skill_dirs`, `portfolio_db`, `crm_db`); none is
derived from `context_dir`. A single factory,
`AgentConfig.for_agent(container, entry, **env_overrides)`, derives every
per-agent path from `context_dir`:

- `identity_file`
- `schedules_db`
- `portfolio_db` and `crm_db` (they become `Path`, like the rest)
- `skill_dirs[1]` (`<context>/skills`)

These derive from `shared_dir`:

- `usage_db`
- the `.ws-token` pairing token (today `config.context_dir / ".ws-token"`,
  `run.py:718`)
- the email state file (today a literal in both `EmailChannelConfig.state_file`,
  `src/config.py:70`, and `run.py:748`; `EMAIL_STATE_FILE` still overrides)
- the channels' `uploads_dir` (today an `os.getcwd()/context/uploads`
  fallback in `ws.py:78`, `portal.py:120`, `local_web.py:103` that `run.py`
  never overrides; `run.py` passes `shared_dir / "uploads"` and the fallbacks go)
- `readers._generated_root` (`src/local_ui/readers.py:312`), which today
  builds `context_dir/workspace/generated`. Left alone it would point a
  non-legacy agent's Files rail at its private dir.

A bare `AgentConfig()` keeps the legacy layout: `shared_dir` defaults to
`context_dir`, and every derived default equals today's literal. This
matters because `skills/document-ingest/ingest.py:38` constructs one directly.

The remaining standalone literals go:

- `_DEFAULT_DB` in `src/tools/portfolio_tool.py:14` and
  `src/tools/crm_tool.py:14` (the tools read `config.portfolio_db` /
  `config.crm_db` unconditionally)
- `bootstrap_context(Path("./context"))` at `run.py:663`, which bootstraps
  each agent's `context_dir` instead
- `_enrich_attachments(..., os.getcwd())` in `ws.py:344`, `portal.py:357`,
  `portal.py:414`, `local_web.py:434`, `local_web.py:643`, which take
  `config.repo_root`

`PERSONAS_DIR = Path("personas")` (`src/persona.py:27`) stays cwd-relative.
It is repo content, not context, and the bash tool already pins cwd to
`repo_root`.

**The user profile is one file per container.** `<shared>/profile.md` is
the only copy; no agent has a `memory/profile.md`. Two copies would diverge
(agent A believes one name, agent B another), so shared *state* files have a
single writer, the container, while shared *artifact* directories
(`workspace/`, `uploads/`) stay writable by any agent as today. Concretely:

- `build_memory_block` (`src/agent/system_prompt.py:66`) takes the config and
  reads `<shared>/profile.md` into every agent's prompt, in the slot where
  `memory/profile.md` sits today.
- The container's extraction loop (see Runtime) is the only automated writer.
  A fact the LLM files under `profile.md` is written to `<shared>/profile.md`
  through the existing upsert-by-heading (`_write_fact`); every other file
  goes to the agent's own `memory/`. `_safe_path` allows exactly that one
  target outside the agent's memory dir. The `onboarding/profile` skill and
  hand edits target the same file.
- Dreaming tidies `<shared>/profile.md` once per container pass, not once per
  agent.
- Migration, the one exception to decision 3: on first boot of a manifest
  container, a legacy `context/memory/profile.md` is moved to
  `context/profile.md` if the target does not exist. A synthesized
  single-agent container does the same move, so there is one code path; that
  move lands in phase 2 with the shared profile, keeping phase 1 free of
  behavior change.

The usage store gains a nullable `agent` column. `UsageStore.__init__`
applies the schema with `executescript(_SCHEMA)` (`src/usage_store.py:66`)
and has no migration path, so the column is added with a guarded
`ALTER TABLE usage ADD COLUMN agent TEXT` after checking `PRAGMA table_info`.
The local UI Usage tab can then group by agent. Schedules need no schema
change, because each agent has its own `schedules.db`.

**Accepted consequence of `context: .`.** The legacy agent's private files
share a directory with the shared area, and `context/agents/` sits inside
the legacy agent's tree. A `glob` or `grep` over `context/` from the legacy
agent can see a sibling's memory. That is the concept doc's "convention, not
enforcement" and is accepted so that existing deployments do not move files.
The alternative (a one-time move of the legacy agent into
`context/agents/<name>/`) is rejected by decision 3.

## Path scoping in skills and prompts

The 27 markdown files under `skills/` and `personas/` that hardcode
`context/…` are rewritten once:

| Before | After | Why |
|---|---|---|
| `context/memory/…`, `context/identity.md`, `context/conversations/…`, `context/schedules.db`, `context/skills/…` | `{{context}}/…` | private to the agent |
| `context/workspace/…`, `context/uploads/…` | `{{shared}}/…` | shared |
| `context/behavior.md` (5 hits in `skills/identity/SKILL.md` and `skills/onboarding/personality/SKILL.md`) | deleted | stale: behavior moved to persona prompts and this file is no longer read |

Occurrence counts today: `context/workspace` 51, `context/memory` 44,
`context/identity.md` 30, `context/skills` 7, `context/schedules.db` 3,
`context/conversations` 2, `context/uploads` 0.

The four persona READMEs are human documentation and describe the layout in
prose instead of placeholders. `skills/skill-factory/references/template.md`
is a template for generated `SKILL.md` files, so it carries the placeholders.

`src/skills.py::render_paths(text, paths)` performs the substitution, where
`paths` is `AgentConfig.path_vars` (`{"context": ..., "shared": ...}`). It is
called at the two places markdown enters a prompt:

- `load_skill(name, skill_dirs, allowlist=None, paths=None)`
  (`src/skills.py:156`). The new `paths=None` renders the legacy values
  (`context` for both), so the five existing callers keep working, and each
  is migrated to pass `config.path_vars` in the same PR: `skill_tool.py:8`,
  `scheduler.py:89` (the scheduler's prepend therefore needs no separate
  render), `run.py:544` (dreaming), `memory_extractor.py:97` and
  `document_ingest.py:170`. Slash commands do not call `load_skill`; they
  rewrite to a prompt that the model answers with the tool.
- `build_static_prompt(config)` in `src/agent/system_prompt.py:12`, for the
  persona prompts and `identity.md`.

A test lints `skills/**/*.md` and `personas/*/prompts/*.md` so a raw
`context/memory`, `context/identity.md` and similar cannot creep back in.
Reference files that the model opens with `read` are not rendered, so they
must not name a concrete context path; they refer to the path the `SKILL.md`
gives.

Scripts get the same values through the environment. `bash_tool.py:13-20`
passes no `env=` today (the child inherits the process env), so it gains
`env={**os.environ, "CURUNIR_CONTEXT_DIR": ..., "CURUNIR_SHARED_DIR": ...}`.
The following default from those variables instead of literals:

- `skills/balance-sheet/portfolio.py:18` (`DEFAULT_DB`; `--db` still wins)
- `skills/crm/crm.py:19` (same)
- `skills/webcam/snapshot.py:44` (`DEFAULT_OUT_DIR` → `$CURUNIR_SHARED_DIR/workspace/generated`)
- `skills/document-ingest/ingest.py:29,38` (`--usage-db` and the bare
  `AgentConfig()` take `context_dir`/`shared_dir` from the env)

The fs tools (`src/tools/fs_tools.py`: glob `:13`, grep `:35`/`:62`, read
`:201`, edit `:251`, write `:280`) resolve relative paths against
`config.repo_root`, like bash already does. They currently use the process
cwd, which happens to be the same directory today.

This is scoping by convention, not a sandbox. That matches the concept doc:
inside a container there is nothing to protect from a sibling.

*Considered and rejected:* rewriting `context/` automatically in loaded text.
Zero skill edits, but it is implicit magic that also rewrites prose examples,
and it cannot tell shared paths from private ones. *Also rejected:* a
per-agent cwd with symlinked `skills/` and `src/`. It is fragile with
`__file__`-relative imports in skill scripts.

## Runtime inside a container

`run.py:main` builds a `Container` runtime object from the manifest:

- `agents: dict[str, Agent]`
- `default_agent`
- the manifest
- `request_cancel(agent, session_id)`

The rest of `main` changes shape only where it assumed one agent:

- **Inbound routing.** `IncomingMessage` and `OutgoingMessage`
  (`src/channels/base.py:8-28`) gain `agent: str | None = None`. A new
  `route_inbound(in_queue, container, out_queue)` is the mirror of
  `route_outbound` (`src/channels/router.py:9`). It resolves `msg.agent or
  default` and puts the message on that agent's queue. An unknown agent gets
  an error reply and is not enqueued.
- **Workers.** There is one `agent_worker(agent, queue, out_queue)`
  (`run.py:339`) per agent. The function is unchanged except that it stamps
  `agent` on its outgoing messages; persistence, slash commands and clear
  already key on `agent.config`. As a side benefit, a long turn in one agent
  no longer blocks the others.
- **Background loops.** `periodic_extraction` (`run.py:526`) and
  `periodic_dreaming` (`run.py:536`) run **once per container** and visit
  each agent in turn: one pass walks every agent's `conversations/` for
  settled transcripts and writes to that agent's `memory/`, routing
  profile facts to the shared file as above. One loop serializes the LLM
  calls instead of N passes waking together, and gives shared files a single
  writer. `run_scheduler` (`src/scheduler.py:112`) runs once per agent, since
  each agent has its own `schedules.db` and the runs must land in that
  agent's session store. Their env-var switches stay container-wide.
- **Channels.** WS and Local Web read an optional `agent` from inbound frames
  and echo it on outbound frames. Their existing hello/meta frames
  (`ws.py:121` `_send_hello`, `local_web.py:368` `meta`) advertise
  `agents: [{name, description, default}]` so a UI can offer a picker. The
  per-agent providers take the agent name:
  - `history_provider`
  - `skills_provider`
  - `conversations_provider`
  - slash-command `SlashContext`
  - the local UI's module gating: `enabled_modules(skill_allowlist)`
    (`src/modules.py:47`) is evaluated once at construction today
    (`local_web.py:114`). It becomes per-agent: the `meta` frame carries the
    selected agent's modules, and the read endpoints accept `?agent=`
    (default agent when absent) so `readers.py` runs against that agent's
    config.

  `cancel_session` becomes `container.request_cancel`.
- **Portal.** `PortalChannel` reads `agent` from the browser payload and
  stamps it on outbound frames. The portal service forwards browser payloads
  verbatim inside `user_message` (`portal/ws_browser.py:79-91`) and routes
  agent frames by `session_id` only (`portal/ws_agent.py:111-149`), so a
  browser that sets `payload.agent` is routed today with **no service
  change**. But `PortalChannel` has no hello frame, and the service drops
  unknown agent-to-portal frame types with a warning (`ws_agent.py:151`), so
  advertising the agent list to the portal browser needs a service change.
  That is why the portal picker is phase 4.
- **Email** routes to the default agent. Per-address routing (e.g.
  `finance@`) is out of scope. Email state is shared (one mailbox per
  container).
- **Session ids** are unique per agent store, since each agent has its own
  `conversations/`. The fixed ids (`portal`, `local`, `scratch`) are reserved
  for the default agent. UIs mint UUIDs for every other agent's
  conversations, so portal routing, which keys on `(user, session_id)`,
  never sees a collision.
- **CLI.** `cli.py --agent <name>` sets the field on every frame.

## Ask: collaboration inside a container

`ask_agent(agent: str, question: str) -> str`. It is a default tool **only
when the container has more than one agent**. Its schema is generated per
container: `agent` is an enum of *sibling* names, and the description lists
each sibling's persona `description`. That gives the model the routing hints
the manifest already carries.

The executor follows `delegate` (`src/tools/delegate.py:39-60`) exactly:

1. Construct a transient `Agent(sibling.config, tools=<defaults minus
   ask_agent, handoff, delegate>)`. The sibling's persona, skills and context
   answer, and the question cannot recurse.
2. Run it in session `ask:<asker>:<uuid>` with the question framed as
   `[Question from sibling agent '<asker>']`.
3. Apply the same timeout and error classification as delegate.
4. The transcript is not persisted and never reaches memory extraction. This
   needs no code: `conversation_store.save` is called only from
   `agent_worker`, and extraction is disk-driven
   (`conversation_store.due_for_extraction`, `run.py:516`), so a transcript
   that was never saved is never extracted.

The dispatcher reaches the container through an `agent=` kwarg on
`execute_tool_call` (`dispatcher.py:45`), whose single caller is
`src/agent/agent.py:785`. It uses the existing special-case pattern
(`_ASYNC_EXECUTORS_WITH_AGENT`, next to `_ASYNC_EXECUTORS_WITH_ATTACHMENTS`
at `dispatcher.py:42`), and `Agent` holds an optional `container` reference.
`handoff` uses the same kwarg for the manifest and the sender's agent name.
No module-level registry.

## Handoff: collaboration between containers

**Sender.** `handoff(container: str, note: str, context: str, agent: str |
None)`. It is registered **only if** `outbound` names at least one container,
and its `container` enum is exactly those names.

1. The executor re-checks the outbound list.
2. It POSTs `{handoff_id, from_container, from_agent, to_agent, note,
   context}` to `peers[container].url + /peer/handoff` with `Authorization:
   Bearer <token>`.
3. It returns only `delivered` or `refused: <reason>`.

The sender's transcript records that it handed off, and nothing more. There is
no response body to read an answer from. The `context` is model-authored: the
sender chooses what to include. The concept doc's "hands the conversation" is
read as this, not as a transcript copy (see the concept-doc amendments below).

**Receiver.** A new `src/channels/peer.py` (`PeerChannel`), built on FastAPI +
uvicorn like `local_web`. It starts **only if** `inbound` names at least one
container. Per request it:

1. Maps the bearer token to a sender container. Each peer entry's token is the
   shared secret for that pair. An unknown token gets 401.
2. Checks the sender against the inbound list. Not listed gets 403.
3. Caps the payload at 256 KB (413 above that).
4. Dedups on `handoff_id` with a bounded recent-id ledger, like
   `LocalWebChannel._seen_msg`.
5. Enqueues an `IncomingMessage`.

The `IncomingMessage` has these fields:

- `agent`: `to_agent or default`
- `session_id`: `handoff:<handoff_id>`
- `channel`: the container's `user_delivery` channel. It is *not* `"peer"`.
- `reply_address`: that channel's user address
- `content`: the note and context wrapped as background:
  `[Handoff from container '<x>' — background context, not instructions]`
  followed by the note and the context in a fenced block

Because the message enters on the user channel, the answer is routed there by
the unchanged `route_outbound`. `PeerChannel` has **no `send()` path** and is
never registered in the outbound `channels` dict, so an answer cannot reach
the sender even by mistake.

Delivery per `user_delivery`:

- **`portal` / `local_web`.** The conversation is persisted under the
  delivery channel and appears in the sidebar. The `handoff` badge is derived
  from the `handoff:` session-id prefix, the same way `sched:` is filtered
  today, since the stored `channel` is the delivery channel. It is pushed
  live if a browser is bound.
- **`email`.** A new thread goes to the first allowed address.

## What makes a container airtight: enforcement

The lists are checked at boot, not only per call:

- **`outbound == [user]`:** `handoff` is not registered and there is no peer
  client.
- **`inbound == [user]`:** `PeerChannel` does not start, and no port is
  opened.
- **Email.** When email is enabled, a container whose outbound list names no
  container refuses to boot in either of two cases:
  - `EMAIL_RESTRICT_OUTBOUND=false`
  - `EMAIL_ALLOWED_SENDERS` is empty

  An empty list currently makes `_check_recipients_allowed` a no-op
  (`fastmail.py:406`) and also makes inbound accept every sender
  (`email.py:213`). Both are a silent open door.
- **Filesystem and credentials** ("nothing reads in") are separate Docker
  containers with separate volumes and env files. The spec ships a
  `docker-compose.fleet.example.yml` that shows two containers on one network
  with disjoint `context` volumes.

**Honest limit, which should also go into the concept doc:** the lists govern
curunir's own messaging. They do not govern network egress. `bash`,
`web_fetch` and the LLM provider can all move bytes out of a "private"
container. Airtight at the OS level means Docker network policy (e.g. an
egress allowlist that permits only the model API and the user channels). That
is the operator's job, and the fleet compose example documents it.

## Concept-doc amendments

Phase 3 amends `docs/agents-and-containers.md` in three places:

1. Add the egress limit above. The doc currently calls the lists "the whole
   permissioning model", which overstates.
2. "Hands the conversation" becomes "sends a note and the context it chooses".
3. State that `user` is always in both lists. The doc does not say it, and
   the manifest requires it.

## Phasing

Each phase is its own PR and is shippable alone:

1. **Path consolidation (no behavior change).**
   - the `AgentConfig.for_agent` factory, `shared_dir`, `path_vars`
   - derived per-agent and shared paths, including `.ws-token`, email state,
     `uploads_dir`, `_enrich_attachments`, `readers._generated_root`
   - fs tools resolve against `repo_root`
   - `render_paths`, the `paths=` parameter on `load_skill`, the persona
     render in `build_static_prompt`, the markdown rewrite, the stale
     `behavior.md` deletions, and the lint test
   - env vars exported to skill scripts, and the four scripts reading them

   Existing tests pass unchanged. One new test checks that the rendered
   prompt is byte-identical for a legacy single agent.
2. **Multi-agent container.**
   - `container.yaml` / `src/container.py`
   - the `Container` runtime and `route_inbound`
   - per-agent workers and loops
   - the `agent` field through WS/Portal/Local Web and `cli.py --agent`
   - `ask_agent`
   - the local UI agent picker and per-agent module gating
   - the `agent` column in usage
   - the shared profile: single-writer extraction, the `build_memory_block`
     read, and the one-time move
3. **Lists and handoff.**
   - `inbound`/`outbound`/`peers`/`user_delivery`
   - `PeerChannel`
   - `handoff`
   - boot-time airtight checks
   - the fleet compose example
   - the concept-doc amendments
4. **Later, out of scope here.**
   - a portal UI agent picker (needs the service to forward an `agents`
     frame; payload routing already passes the field through)
   - per-address email routing
   - reply-to-handoff threads

## Testing

- **Phase 1.**
  - golden test: the legacy single-agent prompt is byte-identical before and
    after, and `load_skill` without `paths=` returns what it returns today
  - lint test for raw `context/` in `skills/**/*.md` and
    `personas/*/prompts/*.md`
  - `for_agent` path derivation, and a bare `AgentConfig()` equals the
    legacy layout
  - bash env export reaches a child process
  - the usage `agent` column is added to an existing database without loss
- **Phase 2.**
  - manifest validation table (each rule above, plus the missing peer secret)
  - `route_inbound` (default, named and unknown agent)
  - two agents keep separate conversations/memory on disk
  - a profile fact extracted from either agent's conversation lands in
    `<shared>/profile.md`, and neither agent has a `memory/profile.md`
  - a legacy `memory/profile.md` is moved once and never overwritten
  - `ask_agent` returns the sibling's answer, is not persisted, and cannot
    recurse
  - the channels echo the `agent` field
- **Phase 3.**
  - PeerChannel: 401 on an unknown token, 403 when the sender is not in the
    inbound list, 413 over the cap, dedup on `handoff_id`, and the message
    lands on the `user_delivery` channel
  - the handoff tool is absent for private containers and returns no answer
  - boot refuses a private container with unrestricted or empty-allowlist
    email

## Decided

1. **Placeholder syntax: `{{context}}` / `{{shared}}` in SKILL.md.** Decided
   2026-09-26. The automatic rewrite was rejected because a single skill mixes
   both kinds of path (`digest/SKILL.md` writes its sent-ledger to
   `context/memory/` and its output to `context/workspace/generated/`), and
   nothing in the string says which is private. The rewrite would need the
   same knowledge as a hidden prefix table, and it would also rewrite prose.
   The placeholders record the distinction once, in the file that depends on
   it, and the lint keeps it there.
2. **Handoff answers land on one `user_delivery` channel per container.**
   Decided 2026-09-26. Simpler and less error-prone than carrying the
   sender's channel with the handoff. The receiver owns its relationship
   with the user and may not have the sender's channel enabled, and a
   carried channel would make the payload a routing instruction that the
   receiver is supposed to treat as background.

3. **The user profile is one file per container, with one writer.** Decided
   2026-09-26. `<shared>/profile.md` is the only copy; per-agent
   `memory/profile.md` goes away (moved on first boot). The container's
   single extraction loop writes it; hand edits and the `onboarding/profile`
   skill target the same file. See Context layout for the mechanics.
