# Admin "show container token" action — design

## Problem

A user's container token (`CURUNIR_PORTAL_TOKEN`) is displayed exactly once,
in the yellow box on the admin page right after the user is created. After
that the value is no longer visible anywhere in the UI. If an admin needs to
re-supply the token to a user (lost it, configuring a new container), the only
option today is "rotate container", which invalidates the previous token and
forces every existing container for that user to be reconfigured.

The admin needs a way to re-reveal the *current* container token without
rotating it.

## Solution

Add a per-user "show container token" action to the admin screen, mirroring the
existing "show sign-in link" action. The container token is already stored as
plaintext in the `users` table, so re-displaying it requires no schema or
storage changes.

## Changes

### `portal/admin.py`

New route, paralleling `admin_show_signin_link`:

```
POST /admin/users/{user_id}/show-container-token
```

- Depends on `admin_user` (admin-only, 403 otherwise).
- CSRF-verified via `_verify_csrf_form`.
- Fetches the target via `db.get_user_by_id(user_id)`; raises 404 if missing.
- Re-renders `admin.html` with the same context shape as the other render
  paths: `new_container_token = target.container_token`,
  `new_user_email = target.email`, `new_signin_link = None`, plus `users` and
  a fresh `csrf_token`.

### `portal/templates/admin.html`

Add one inline form in the actions cell, next to "rotate container":

```html
<form class="inline" method="post" action="/admin/users/{{ u.id }}/show-container-token">
  <input type="hidden" name="csrf" value="{{ csrf_token }}">
  <button>show container token</button>
</form>
```

No change to the yellow-box markup: it already renders `new_container_token`
when present (with the "shown once" wording — see Note below).

### Note on yellow-box wording

The yellow box currently says the container token is "shown once". With this
feature the token can be shown again on demand, so the "shown once" phrasing is
no longer accurate. Update that line to drop "shown once" — e.g. "Container
token (set as `CURUNIR_PORTAL_TOKEN` on their curunir):".

### Tests (`portal/tests/`)

Add tests for the new route, paralleling the existing show-signin-link tests:

- Admin POST with valid CSRF returns 200 and the user's `container_token`
  appears in the response body.
- POST without/with invalid CSRF returns 403.
- POST for an unknown `user_id` returns 404.

## Out of scope

- No DB or schema changes.
- No change to token storage (remains plaintext, as today).
- No full env-config snippet — the box shows the token value only.
