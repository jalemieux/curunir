# Deployment

Curunir ships as a versioned container image. Hosts pull a tag from GHCR
instead of cloning the repo and running `docker compose up --build`. The
heavy image build (apt, pandoc, texlive, chromium, npm globals) happens
once in CI per commit; every host pulls the same artifact.

The portal is a separate service that runs on **render.com**. It has its
own image (`ghcr.io/jalemieux/curunir-portal`) and is not part of the
host-side SSH fan-out described below.

This doc assumes a host that's already provisioned (Docker installed, a
deploy user with docker-group access, GHCR credentials). For standing up a
fresh/bare Ubuntu box from scratch — decommissioning prior services, SSH
keys, the docker group, per-instance deploy-dir conventions, operational
gotchas, and the auto-updater — see
[`ubuntu-server-setup.md`](ubuntu-server-setup.md).

## Image tagging

CI publishes both images on every push to `main` and on `v*` tags. Tags
emitted by `.github/workflows/build-image.yml`:

| Tag             | When                                  | Use                                 |
| --------------- | ------------------------------------- | ----------------------------------- |
| `latest`        | every push to `main`                  | dev / unpinned hosts                |
| `sha-<short>`   | every push                            | pin a specific commit on a host     |
| `vX.Y.Z`        | push of a matching git tag            | named release                       |

Both `ghcr.io/jalemieux/curunir` and `ghcr.io/jalemieux/curunir-portal`
move together because they're built in the same workflow run.

Multi-arch (`linux/amd64` + `linux/arm64`) — works on cloud x86 hosts and
Apple Silicon. Verify a manifest with:

```bash
docker buildx imagetools inspect ghcr.io/jalemieux/curunir:latest
```

## curunir hosts

### One-time setup per host

1. **Log in to GHCR** (the images are private — needs a PAT with
   `read:packages`):

   ```bash
   echo "$GHCR_PAT" | docker login ghcr.io -u jalemieux --password-stdin
   ```

2. **Create the deploy directory.** Hosts run from `~/curunir-deploy/`,
   which holds the compose file and bind-mount targets — no source
   checkout needed:

   ```
   ~/curunir-deploy/
   ├── docker-compose.yml      # copy from repo root
   ├── .env                    # API keys, CURUNIR_TAG, etc.
   ├── secrets/                # OAuth tokens, service-account JSON
   ├── workspace/              # persisted workspace (curunir.log lives here)
   └── context/                # identity.md, memory/, schedules.db
   ```

   `docker-compose.yml` is the only file that needs to be kept in sync
   with the repo. The simplest way is to copy it on first setup and
   re-copy after meaningful changes (env vars, volumes, ports). The image
   tag is decoupled from the compose file — bumping `CURUNIR_TAG` in
   `.env` doesn't require touching `docker-compose.yml`.

3. **Sync `context/`** with `./sync-context.sh` from a workstation, the
   same as before.

### Deploying a new image

Pin a tag in `~/curunir-deploy/.env`:

```bash
# ~/curunir-deploy/.env
CURUNIR_TAG=sha-abc1234     # or `latest`, or a release tag like `v1.2.3`
```

Then pull + restart just the curunir service:

```bash
cd ~/curunir-deploy
docker compose pull curunir
docker compose up -d curunir
```

To fan out to every target listed in `scripts/.deploy-hosts`:

```bash
# from a workstation, inside the repo
scripts/deploy.sh sha-abc1234
```

`scripts/deploy.sh` ssh's into each target and runs the pull + restart
above. It only touches the curunir service.

Each line in `scripts/.deploy-hosts` is one target. The deploy dir
defaults to `~/curunir-deploy`, but can be overridden per line — useful
when one host runs multiple curunir instances side-by-side:

```
jac@alpha.example.com                          # ~/curunir-deploy
jac@gamma.example.com  ~/curunir-projectA
jac@gamma.example.com  ~/curunir-projectB
```

Distinct deploy dirs give distinct compose project names automatically,
so the instances don't clobber each other's containers.

### Rollback

Pulled images stay on disk until pruned. To roll back, set `CURUNIR_TAG`
back to the previous `sha-...` (or release tag) in `.env` and `docker
compose up -d curunir` — no re-pull needed.

```bash
cd ~/curunir-deploy
sed -i 's/^CURUNIR_TAG=.*/CURUNIR_TAG=sha-deadbeef/' .env
docker compose up -d curunir
```

### Migrating from the old `git clone` setup

For each host currently running curunir via `git pull && docker compose
up --build`:

1. `docker login ghcr.io` (see above).
2. Create `~/curunir-deploy/` and copy `docker-compose.yml`, `.env`,
   `secrets/`, `workspace/`, and `context/` from the old checkout.
   Re-using the existing volumes preserves logs and memory.
3. Set `CURUNIR_TAG` in `.env`.
4. `docker compose pull curunir && docker compose up -d curunir`.
5. Verify the container starts and the agent boots:

   ```bash
   docker compose ps
   docker exec curunir tail -n 50 /app/workspace/curunir.log
   curl -i ws://localhost:8765   # (or use the cli.py client)
   ```

6. Once stable, delete the old `git clone` directory. Do one host first,
   confirm it works, then fan out with `scripts/deploy.sh`.

## Log management

A long-running host produces three logs, each bounded by a **different**
mechanism. Don't stack a second rotator on a log that already self-rotates
— two rotators fighting over one inode lose lines.

| Log | Where | Bounded by | Cap |
| --- | --- | --- | --- |
| Container stdout/stderr | `/var/lib/docker/containers/<id>/*-json.log` (root) | `logging:` block in `docker-compose.yml` | `max-size 10m × max-file 5` ≈ 50MB |
| `workspace/curunir.log` | the workspace bind-mount | the app's Python `RotatingFileHandler` (in `run.py`) | `10MB × 3 backups` ≈ 40MB |
| `update-check.log` | the deploy dir (auto-updater hosts only) | host-side `logrotate` cron — see below | weekly × 4, compressed |

### Container log (the easy one to forget)

The default `json-file` driver is **unbounded** — it grows until it fills
`/var`, and it's invisible because it lives under root-owned
`/var/lib/docker`. This is the log that actually takes a long-running host
down. It's capped by the `logging:` block on the `curunir` service in
`docker-compose.yml`, so every host inherits the cap just by copying the
compose file. Keeping it in compose (not a global `/etc/docker/daemon.json`)
means it survives `docker compose up -d` recreates, including auto-updaters
that recreate the container on a new image. Verify it's live on a host:

```bash
docker inspect --format '{{json .HostConfig.LogConfig}}' <container>
# → {"Type":"json-file","Config":{"max-file":"5","max-size":"10m"}}
```

If you ever want the cap to apply to *every* container on a host (not just
curunir), set it globally instead — needs root:

```json
// /etc/docker/daemon.json   (then: sudo systemctl restart docker)
{ "log-driver": "json-file", "log-opts": { "max-size": "10m", "max-file": "5" } }
```

### `workspace/curunir.log` — leave it alone

`run.py` already rotates this via `RotatingFileHandler` (10MB × 3 →
`curunir.log.1/.2/.3`). **Do not** add a `logrotate` rule for it — the two
rotators would race on the same file. The cap is configured in code, not env.

### `update-check.log` (auto-updater hosts)

Hosts that run the Watchtower-style auto-updater (`curunir-update-check.sh`
on a `*/15` cron — a host-side addition, not part of the canonical deploy)
produce `update-check.log`. The script appends to it on every run. Bound it
with a **user-level** `logrotate` (the log is user-owned, so no root needed):

1. Drop a config next to the script (`logrotate-curunir.conf`):

   ```
   /home/<user>/<deploy-dir>/update-check.log {
       weekly
       rotate 4
       compress
       delaycompress
       missingok
       notifempty
       copytruncate
   }
   ```

   `copytruncate` matters: the cron keeps the file open via `>>`, so
   logrotate copies-then-truncates in place rather than renaming an inode
   the writer still holds.

2. Run it from the **user** crontab (state file makes the `weekly` cadence
   work without root or `/etc/logrotate.d`):

   ```cron
   30 4 * * * /usr/sbin/logrotate --state <deploy-dir>/.logrotate.state \
       <deploy-dir>/logrotate-curunir.conf 2>&1 | logger -t curunir-logrotate
   ```

   Piping to `logger` sends logrotate's own output to journald (already
   rotated by systemd) instead of creating yet another growing file.

3. If the updater script self-trims with `tail -n 1000`, **remove that** —
   it keeps the file too small for logrotate to ever fire and loses history
   instead of archiving it. Let logrotate own the bounding.

Validate without waiting a week:

```bash
logrotate --debug --state <state> <conf>   # dry-run, no changes
logrotate --force --state <state> <conf>   # prove it: creates update-check.log.1
```

## portal (render.com)

The portal is deployed on render.com as a Docker service that pulls
`ghcr.io/jalemieux/curunir-portal:<tag>` from GHCR.

### One-time setup

In the render dashboard, the `curunir-portal` service needs:

- **Image source:** registry credentials configured for `ghcr.io` (the
  same GHCR PAT used by curunir hosts, scoped to `read:packages`).
- **Image:** `ghcr.io/jalemieux/curunir-portal:<tag>`.
- **Env vars:** as documented in `portal/render.yaml` (`DATABASE_URL`,
  `PORTAL_SECRET_KEY`, `PORTAL_BASE_URL`, `ADMIN_EMAILS`).

### Deploying a new image

Bump the image tag in the render service settings (UI or render API) and
trigger a redeploy. Render pulls the new image and rolls the service.
Once portal is back, the curunir container's `PortalChannel`
reconnects automatically.

To roll back, point the render service at the previous `sha-...` tag and
redeploy.

### Local-dev portal

For local-dev portal work, run with the dev override:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile portal up --build
```

The override re-adds `build: ./portal`, the `./portal:/app/portal` bind
mount, and `uvicorn --reload`, so portal edits hot-reload during
development.

## Risks and notes

- **Private images:** if `ghcr.io/jalemieux/curunir*` are private, every
  host (and render.com) needs PAT-based credentials. If they're made
  public, only push needs auth.
- **Cache size:** GitHub Actions caches per repo are capped at 10 GB.
  texlive + chromium layers are heavy; if cache evictions cause slow
  builds, switch `cache-to: type=gha,mode=max` to `mode=min` or move to
  a registry-backed cache.
- **`v*` tags:** the workflow handles them when they appear; no release
  process is wired up yet.
