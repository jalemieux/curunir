# Deployment

Curunir ships as a versioned container image. Hosts pull a tag from GHCR
instead of cloning the repo and running `docker compose up --build`. The
heavy image build (apt, pandoc, texlive, chromium, npm globals) happens
once in CI per commit; every host pulls the same artifact.

The portal is a separate service that runs on **render.com**. It has its
own image (`ghcr.io/jalemieux/curunir-portal`) and is not part of the
host-side SSH fan-out described below.

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

## portal (render.com)

The portal is deployed on render.com as a Docker service that pulls
`ghcr.io/jalemieux/curunir-portal:<tag>` from GHCR.

### One-time setup

In the render dashboard, the `curunir-portal` service needs:

- **Image source:** registry credentials configured for `ghcr.io` (the
  same GHCR PAT used by curunir hosts, scoped to `read:packages`).
- **Image:** `ghcr.io/jalemieux/curunir-portal:<tag>`.
- **Env vars:** as documented in `portal/render.yaml` (`DATABASE_URL`,
  `PORTAL_SECRET_KEY`, `PORTAL_BASE_URL`, `EMAIL_API_KEY`, `EMAIL_FROM`,
  `ADMIN_EMAILS`).

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
