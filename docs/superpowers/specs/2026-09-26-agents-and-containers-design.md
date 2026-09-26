# Agents and Containers — Design

**Date:** 2026-09-26
**Status:** Draft, awaiting review
**Related:** PR #546 (concept: `docs/agents-and-containers.md`), `2026-05-29-persona-deployment-design.md`

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
- Every tool receives `config`.
- The memory extractor, conversation store, scheduler and skill allowlist all
  take `config` / `context_dir`.

What is hard-wired to one agent is `run.py` wiring, a handful of path
literals, and ~31 skill/persona markdown files that spell `context/memory`,
`context/identity.md` and `context/workspace` literally.

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
`src/persona.py`: it validates at boot and raises on a malformed manifest.
Validation rules:

- Agent names are unique.
- Exactly one agent is the default (implied when there is one agent).
- Every persona exists.
- `user` appears in both lists.
- Every container named in a list has a `peers` entry.
- `user_delivery` names an enabled channel.

## Context layout

```
context/                         # container root = shared area
  workspace/generated/           # deliverables (shared)
  uploads/  cards/               # staged inputs (shared)
  profile.md                     # shared user profile (new, optional)
  usage.db  email_state.json  .ws-token
  identity.md memory/ conversations/ schedules.db   # ← legacy agent (context: .)
  agents/
    finance/
      identity.md
      memory/            (incl. portfolio.db, crm.db, profile.md)
      conversations/
      schedules.db
```

`AgentConfig` gains `agent_name` and `shared_dir`. A single factory,
`AgentConfig.for_agent(container, entry, **env_overrides)`, derives every
per-agent path from `context_dir`:

- `identity_file`
- `schedules_db`
- `portfolio_db`
- `crm_db`
- `skill_dirs[1]` (`<context>/skills`)

`usage_db` derives from `shared_dir`. The standalone path literals go:

- `portfolio_tool` / `crm_tool` `_DEFAULT_DB`
- the channel `os.getcwd()/context/uploads` constructors, which take
  `shared_dir` instead
- `bootstrap_context(Path("./context"))`, which bootstraps each agent's
  `context_dir`

`build_memory_block` also includes `<shared>/profile.md` when it exists. That
is the "shared area holds the user's profile" from the concept doc. The
per-agent `memory/profile.md` keeps working.

The usage store gains a nullable `agent` column (additive migration). The
local UI Usage tab can then group by agent. Schedules need no schema change,
because each agent has its own `schedules.db`.

## Path scoping in skills and prompts

The ~31 markdown files under `skills/` and `personas/` that hardcode
`context/…` are rewritten once:

| Before | After | Why |
|---|---|---|
| `context/memory/…`, `context/identity.md`, `context/conversations/…`, `context/schedules.db`, `context/skills/…` | `{{context}}/…` | private to the agent |
| `context/workspace/…`, `context/uploads/…` | `{{shared}}/…` | shared |

`src/skills.py::render_paths(text, config)` performs the substitution. It is
called at the three places markdown enters a prompt:

- `load_skill`
- `build_static_prompt` (persona prompts)
- the scheduler's skill prepend

A test lints `skills/` and `personas/` so a raw `context/memory`,
`context/identity.md` and similar cannot creep back in.

Scripts get the same values through the environment. The bash tool exports
`CURUNIR_CONTEXT_DIR` and `CURUNIR_SHARED_DIR` into its subprocess env. The
following default from those variables instead of literals:

- `skills/balance-sheet/portfolio.py`
- `skills/crm/crm.py`
- `skills/webcam/snapshot.py`
- `skills/document-ingest/ingest.py`

The fs tools (`read`/`write`/`edit`/`glob`/`grep`) resolve relative paths
against `config.repo_root`, like bash already does. They currently use the
process cwd, which happens to be the same directory today.

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

- **Inbound routing.** `IncomingMessage` and `OutgoingMessage` gain
  `agent: str | None = None`. A new `route_inbound(in_queue, container,
  out_queue)` is the mirror of `route_outbound`. It resolves `msg.agent or
  default` and puts the message on that agent's queue. An unknown agent gets
  an error reply and is not enqueued.
- **Workers.** There is one `agent_worker(agent, queue, out_queue)` per agent.
  The function is unchanged except that it stamps `agent` on its outgoing
  messages. As a side benefit, a long turn in one agent no longer blocks the
  others.
- **Background loops.** `periodic_extraction`, `periodic_dreaming` and
  `run_scheduler` run once per agent. They already take an `Agent`. Their
  env-var switches stay container-wide.
- **Channels.** WS, Portal and Local Web read an optional `agent` from inbound
  frames and echo it on outbound frames. The hello/meta frame advertises
  `agents: [{name, description, default}]` so a UI can offer a picker. The
  per-agent providers take the agent name:
  - `history_provider`
  - `skills_provider`
  - `conversations_provider`
  - slash-command `SlashContext`
  - the local UI's module gating (`enabled_modules` becomes per-agent)

  `cancel_session` becomes `container.request_cancel`. The portal service
  needs **no change**. It forwards browser payloads verbatim
  (`ws_browser.py:90`) and routes agent frames by `session_id` only.
- **Email** routes to the default agent. Per-address routing (e.g.
  `finance@`) is out of scope.
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

The executor follows `delegate` exactly:

1. Construct a transient `Agent(sibling.config, tools=<defaults minus
   ask_agent, handoff, delegate>)`. The sibling's persona, skills and context
   answer, and the question cannot recurse.
2. Run it in session `ask:<asker>:<uuid>` with the question framed as
   `[Question from sibling agent '<asker>']`.
3. Apply the same timeout and error classification as delegate.
4. Do not persist the transcript.

The dispatcher reaches the container through an `agent=` kwarg. It uses the
existing special-case pattern (`_ASYNC_EXECUTORS_WITH_AGENT`, next to
`_ASYNC_EXECUTORS_WITH_ATTACHMENTS`), and `Agent` holds an optional
`container` reference. No module-level registry.

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
no response body to read an answer from.

**Receiver.** A new `src/channels/peer.py` (`PeerChannel`), built on FastAPI +
uvicorn like `local_web`. It starts **only if** `inbound` names at least one
container. Per request it:

1. Maps the bearer token to a sender container. Each peer entry's token is the
   shared secret for that pair. An unknown token gets 401.
2. Checks the sender against the inbound list. Not listed gets 403.
3. Caps the payload size.
4. Dedups on `handoff_id`.
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

- **`portal` / `local_web`.** The conversation is persisted with channel
  badge `handoff` and appears in the sidebar. It is pushed live if a browser
  is bound.
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
  (`fastmail.py:406`), which is a silent open door.
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

## Phasing

Each phase is its own PR and is shippable alone:

1. **Path consolidation (no behavior change).**
   - the `AgentConfig.for_agent` factory
   - derived per-agent paths
   - `shared_dir`
   - fs tools resolve against `repo_root`
   - `render_paths` and the skill/persona markdown rewrite, with the lint test
   - env vars exported to skill scripts

   Existing tests pass unchanged. One new test checks that the rendered
   prompt is byte-identical for a legacy single agent.
2. **Multi-agent container.**
   - `container.yaml` / `src/container.py`
   - the `Container` runtime and `route_inbound`
   - per-agent workers and loops
   - the `agent` field through WS/Portal/Local Web and `cli.py --agent`
   - `ask_agent`
   - the local UI agent picker
   - the `agent` column in usage
3. **Lists and handoff.**
   - `inbound`/`outbound`/`peers`/`user_delivery`
   - `PeerChannel`
   - `handoff`
   - boot-time airtight checks
   - the fleet compose example
   - the concept-doc amendment on egress
4. **Later, out of scope here.**
   - a portal UI agent picker (the service already passes the field through)
   - per-address email routing
   - reply-to-handoff threads

## Testing

- **Phase 1.**
  - golden test: the legacy single-agent prompt is byte-identical before and
    after
  - lint test for raw `context/` in markdown
  - `for_agent` path derivation
  - bash env export
- **Phase 2.**
  - manifest validation table
  - `route_inbound` (default, named and unknown agent)
  - two agents keep separate conversations/memory on disk
  - `ask_agent` returns the sibling's answer, is not persisted, and cannot
    recurse
  - the channels echo the `agent` field
- **Phase 3.**
  - PeerChannel: 401 on an unknown token, 403 when the sender is not in the
    inbound list, dedup on `handoff_id`, and the message lands on the
    `user_delivery` channel
  - the handoff tool is absent for private containers and returns no answer
  - boot refuses a private container with unrestricted or empty-allowlist
    email

## Open questions

1. **Placeholder syntax.** Is `{{context}}` / `{{shared}}` acceptable in
   SKILL.md, or would you rather keep the literals and accept the automatic
   rewrite?
2. **Where handoff answers land.** Is one `user_delivery` channel per
   container right, or should the sender's user channel travel with the
   handoff? The latter leaks less structure but couples the containers.
3. **Shared profile.** Should `context/profile.md` be written by the memory
   extractor, or only by hand?
