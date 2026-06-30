# Provisioning a bare Ubuntu host for curunir

This is the layer *beneath* [`deployment.md`](deployment.md). `deployment.md`
assumes a host that already has Docker, a deploy user with docker access, and
GHCR credentials. This runbook is how you get there from a fresh (or
repurposed) Ubuntu box, plus the operational gotchas and the auto-updater that
aren't part of the app-level deploy flow.

Worked example throughout: a minimal **Ubuntu 24.04** box reachable as
`curunir.local`, running a single instance under the deploy dir
`~/curunir_deployments/charles@curunir.ai/`. Substitute your own
hostname/identity.

---

## 0. Assess before you change anything

Inspect the box before touching it — a repurposed machine may be running
services you don't expect (this one was acting as a WiFi access point /
router). Don't blind-`pkill`; look first.

```bash
# What's actually running / listening / scheduled
ps -eo pid,ppid,user,etime,cmd | grep -iE "python|docker|uvicorn|run\.py" | grep -v grep
ss -ltnp                      # listening sockets (sudo for process names)
systemctl list-unit-files --state=enabled
uptime                        # recent reboot? explains why things aren't running
ip -br addr                   # interfaces — know which one your SSH rides on
```

Key safety check: **know which interface your SSH session uses** before
disabling any networking service. `echo $SSH_CONNECTION` and `ip route get
<your-client-ip>` tell you the path out. Disabling a service on a *different*
interface won't cut your connection.

---

## 1. Decommission unwanted services

This box shipped as a WiFi AP/router (`hostapd` on `wlo1` broadcasting an
SSID, plus `dnsmasq` for DHCP/DNS and an `nginx` captive portal). Since our
SSH rode the wired `enp1s0` (different interface), disabling the AP was safe:

```bash
sudo systemctl disable --now hostapd        # stop now + never start on boot
sudo systemctl disable dnsmasq nginx        # were already failed; stop boot attempts
```

`disable --now` = stop immediately **and** remove from boot. Verify:

```bash
systemctl is-active hostapd dnsmasq nginx
```

Generalize: stop + disable anything the box was doing that you don't want,
after confirming it's not on your SSH path.

---

## 2. SSH admin access from a workstation

Set up a dedicated key so you (or tooling) get passwordless access instead of
typing the account password every time.

On the **workstation**:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/curunir_admin -N "" -C "curunir-admin"
ssh-copy-id -i ~/.ssh/curunir_admin.pub <user>@<host>     # one password entry
```

Add a config alias so `ssh curunir` just works:

```
# ~/.ssh/config
Host curunir
    HostName curunir.local
    User curunir
    IdentityFile ~/.ssh/curunir_admin
    IdentitiesOnly yes
```

Test non-interactively: `ssh -o BatchMode=yes curunir whoami`.

---

## 3. Docker access for the deploy user

curunir runs as a container, so the deploy user needs Docker. If Docker is
already installed (this box had 29.x + the compose plugin), just add the user
to the `docker` group so you don't need `sudo` for every command:

```bash
sudo usermod -aG docker <user>
```

Group membership is read at login — **open a fresh SSH session** for it to
take effect. Verify: `docker info >/dev/null && echo ok`.

> If Docker isn't installed: `curl -fsSL https://get.docker.com | sh`, then
> the `usermod` above.

---

## 4. GHCR credentials (per-user — common gotcha)

The images are private; the host needs a PAT with `read:packages`. **Docker
stores credentials per-user** (`~/.docker/config.json`), so log in **as the
deploy user**, not as root — a root login won't let the deploy user pull.

```bash
# as the deploy user (NOT root):
echo "$GHCR_PAT" | docker login ghcr.io -u jalemieux --password-stdin
```

Username is the GitHub username (`jalemieux`), never an email. `denied: denied`
means a bad/expired/under-scoped token. If another host already pulls fine,
the fastest fix is to reuse its credential:

```bash
scp <user>@<working-host>:.docker/config.json <user>@<new-host>:.docker/config.json
```

Verify: `docker pull ghcr.io/jalemieux/curunir:latest`.

---

## 5. Deploy directory + compose

Follow [`deployment.md`](deployment.md) for the deploy-dir layout and the
compose file. One convention worth calling out: **the compose project name is
derived from the deploy directory name**, which is how multiple instances
coexist on one host without clobbering each other. We name dirs by identity:

```
~/curunir_deployments/charles@curunir.ai/
├── docker-compose.yml      # copy from repo root
├── .env                    # API keys, channel config, ports
├── context/                # identity.md, memory/, *.db  (bootstrapped on first boot)
├── secrets/                # OAuth tokens (read-only mount)
└── workspace/              # persisted workspace (curunir.log)
```

Scaffold + bring up just the curunir service (postgres/portal are
profile-gated to `["portal"]` and stay off):

```bash
cd ~/curunir_deployments/charles@curunir.ai
mkdir -p secrets workspace context
docker compose up -d curunir
```

`context/` can start empty — `onboarding/bootstrap.py` scaffolds
`memory/`, the SQLite stores, etc. on first boot (it never overwrites files
you place yourself). It does **not** create `identity.md` — drop your own in
for a custom persona, then `docker compose restart curunir`. Until then it
boots a generic default personality (logged as a warning).

### Verify the boot

```bash
docker compose ps
docker compose logs --tail=40 curunir      # look for "Starting N channel(s)" + no traceback
ss -ltn | grep -E ":8767|:8771"            # host ports listening (see your .env mappings)
```

In the worked example the host publishes `8767→8765` (WS/CLI) and
`8771→8766` (local web console). The console URL + token is printed in the
boot log (`http://<host>:8771/?token=...`; token persists in
`context/.ws-token`).

---

## 6. Operational gotchas

**Renaming a deploy dir orphans the running container.** The compose project
is tied to the directory name, but a rename doesn't re-label a *running*
container. After `mv old new`, the live container still belongs to the old
project and a fresh `docker compose up` from the new dir would spin up a
*second* one and clash on ports. Reconcile by tearing down the old project and
recreating under the new name (bind mounts carry all state — no data loss):

```bash
cd ~/curunir_deployments/<new-name>
docker compose -p <old-project-name> down    # remove old-named container + network
docker compose up -d curunir                 # recreate under the new project
```

**Bind-mounted files are root-owned.** The container writes `context/` as
root, so the deploy user can't `rm -rf context/` (subdirs like `memory/` are
`root:root`). To wipe it without host `sudo`, stop the container (release the
mount) and use a throwaway root container:

```bash
cd ~/curunir_deployments/<name>
docker compose down
docker run --rm -v "$PWD":/work alpine \
  sh -c 'rm -rf /work/context && mkdir /work/context && chown 1001:1001 /work/context'
docker compose up -d curunir                 # re-bootstraps a clean context/
```

(`1001:1001` is the deploy user's uid:gid — check with `id`.)

**Reboot survival.** `restart: unless-stopped` in the compose file plus an
enabled `docker.service` means the container comes back after a reboot. If a
box loses its app on reboot, that's the thing to check (`systemctl is-enabled
docker`).

---

## 7. Auto-updater (15-minute poll)

A small cron-driven script polls GHCR and recreates the container when the
`:latest` digest changes (Watchtower-style, but no extra service). Lives in
the deploy dir as `curunir-update-check.sh`:

```bash
#!/usr/bin/env bash
# Polls GHCR for a newer :latest; recreates the container if the digest
# changed. No-op when up to date. Driven by cron every 15 minutes.
set -uo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin

DIR="$HOME/curunir_deployments/charles@curunir.ai"
SERVICE="curunir"
IMG="ghcr.io/jalemieux/curunir:latest"
LOG="$DIR/update-check.log"
LOCK="$DIR/.update-check.lock"
ts() { date -Is; }

exec 9>"$LOCK" || exit 0
flock -n 9 || { echo "$(ts) SKIP: another check running" >>"$LOG"; exit 0; }
cd "$DIR" || { echo "$(ts) ERROR: cd $DIR failed" >>"$LOG"; exit 1; }

before=$(docker image inspect --format '{{.Id}}' "$IMG" 2>/dev/null || echo none)
if ! docker compose pull -q "$SERVICE" >>"$LOG" 2>&1; then
  echo "$(ts) ERROR: pull failed (retry next run)" >>"$LOG"
  tail -n 1000 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"; exit 0
fi
after=$(docker image inspect --format '{{.Id}}' "$IMG" 2>/dev/null || echo none)

if [ "$before" != "$after" ]; then
  echo "$(ts) UPDATE: ${before:0:19} -> ${after:0:19}; recreating" >>"$LOG"
  if docker compose up -d "$SERVICE" >>"$LOG" 2>&1; then
    echo "$(ts) OK: recreated" >>"$LOG"
    docker image prune -f >>"$LOG" 2>&1 || true
  else
    echo "$(ts) ERROR: recreate failed" >>"$LOG"
  fi
else
  echo "$(ts) OK: up to date (${after:0:19})" >>"$LOG"
fi
tail -n 1000 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
```

Install:

```bash
chmod +x ~/curunir_deployments/charles@curunir.ai/curunir-update-check.sh
( crontab -l 2>/dev/null | grep -v "curunir-update-check.sh"; \
  echo "*/15 * * * * $HOME/curunir_deployments/charles@curunir.ai/curunir-update-check.sh" ) | crontab -
crontab -l                                   # confirm
```

Notes:

- **Auto-applies** updates — recreating the container is a few seconds of
  downtime and interrupts any in-flight agent turn. For check-only behavior,
  drop the `docker compose up -d` block and just log/notify on a digest change.
- `flock` prevents overlapping runs; the log self-trims to 1000 lines.
- Watch it: `tail -f ~/curunir_deployments/charles@curunir.ai/update-check.log`.
- Pin a specific tag instead (no auto-update) by setting `CURUNIR_TAG` in
  `.env` per [`deployment.md`](deployment.md) and not installing the cron job.

---

## Quick reference

```bash
# from the workstation
ssh curunir                                   # alias -> curunir@curunir.local

# on the host, in the deploy dir
cd ~/curunir_deployments/charles@curunir.ai
docker compose ps                             # status
docker compose logs -f curunir                # live logs
docker compose restart curunir                # restart (picks up context edits)
docker compose pull curunir && docker compose up -d curunir   # manual update
docker compose down                           # stop + remove (state stays in bind mounts)
tail -f update-check.log                      # auto-updater activity
```
