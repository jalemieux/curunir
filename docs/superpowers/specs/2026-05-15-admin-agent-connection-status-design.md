# Admin — agent connection status

## Problem

The portal admin page (`/admin`) lists users but gives no indication of
which curunir containers ("agents") are currently connected. An admin
cannot tell, from the portal, whether a given user's self-hosted
container is online.

## Goal

Add an **agent** column to the `/admin` users table showing whether each
user's container is connected to the portal, as a **snapshot at
page-load time**. Refreshing the page re-reads status.

## Background

- One container per user. A user's `container_token` authenticates a
  single agent WebSocket at `/ws/agent`.
- `portal/routing.py` holds the in-memory `RoutingTable` singleton
  (`routing`). Per user, `UserRoute.agent_ws` is a live socket when the
  container is connected and `None` otherwise. The table is
  single-process and reset on portal restart.
- The admin page is server-rendered Jinja (`portal/templates/admin.html`),
  rendered by handlers in `portal/admin.py`.

The connection state already exists; it is simply not surfaced.

## Non-goals (YAGNI)

- No live-updating status — snapshot per page load only.
- No last-connected timestamp — the routing table holds no timestamps
  and is memory-only.
- No browser-session count.

## Design

### 1. `portal/routing.py` — read-only accessor

Add to `RoutingTable`:

```python
def online_agent_user_ids(self) -> set[int]:
    """User ids that currently have a connected agent socket."""
    return {uid for uid, r in self._routes.items() if r.agent_ws is not None}
```

No lock — single-threaded asyncio, and a snapshot read is consistent
with the existing lock-free `agent_for` / `browsers_for`.

### 2. `portal/admin.py` — `_render_admin` helper

The four handlers that render `admin.html` (`admin_index`,
`admin_create_user`, `admin_show_signin_link`, `admin_show_container_token`)
each build a near-identical context dict. Introduce one helper:

```python
def _render_admin(request, user, *, users, **extra):
    ctx = {
        "users": users,
        "csrf_token": csrf.issue_csrf(user.id),
        "online_ids": routing.online_agent_user_ids(),
        "new_container_token": None,
        "new_signin_link": None,
        "new_user_email": None,
    }
    ctx.update(extra)
    return templates.TemplateResponse(request, "admin.html", ctx)
```

Each of the four handlers calls `_render_admin(...)`, passing only the
`new_*` overrides it needs. This removes the existing duplication and
puts `online_ids` injection in one place.

### 3. `portal/templates/admin.html` — agent column

Add an `agent` column header and per-row cell. The cell shows a green
**online** badge when `u.id in online_ids`, else a grey **offline**
badge. Minimal inline CSS for the two badge states, consistent with the
existing lightweight style block.

### 4. Tests — `portal/tests/test_admin.py`

Add a test that:
- creates two users,
- registers a fake `Sender`-shaped agent socket in `routing` for one
  user via `routing.register_agent`,
- GETs `/admin` as an admin,
- asserts the connected user renders **online** and the other
  **offline**.

Reset / isolate routing state so the test does not leak into others
(register then unregister, or use a fresh table per the existing test
fixtures).

## Error handling

None required — the feature is a pure read. A user with no routing
entry is simply offline.

## Files touched

| File | Change |
|------|--------|
| `portal/routing.py` | add `online_agent_user_ids()` |
| `portal/admin.py` | add `_render_admin` helper; route 4 handlers through it |
| `portal/templates/admin.html` | add agent column + badge CSS |
| `portal/tests/test_admin.py` | add online/offline rendering test |
