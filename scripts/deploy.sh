#!/usr/bin/env bash
# Fan out an image-pull deploy of the curunir service to every host listed in
# scripts/.deploy-hosts (one `user@host` per line, # comments allowed).
#
# Usage:
#   scripts/deploy.sh                 # pulls :latest on every host
#   scripts/deploy.sh sha-abc1234     # pins a specific tag across all hosts
#   scripts/deploy.sh v1.2.3
#
# Only touches the `curunir` service. The portal lives on render.com and is
# updated separately — see docs/deployment.md.
#
# Each host is expected to have a ~/curunir-deploy/ directory containing the
# repo's docker-compose.yml, an .env, and the bind-mount subdirs
# (secrets/, workspace/, context/). See docs/deployment.md for the one-time
# host setup.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOSTS_FILE="$SCRIPT_DIR/.deploy-hosts"
TAG="${1:-latest}"

if [[ ! -f "$HOSTS_FILE" ]]; then
    echo "error: $HOSTS_FILE not found." >&2
    echo "Copy scripts/.deploy-hosts.example to scripts/.deploy-hosts and add one user@host per line." >&2
    exit 1
fi

# Strip blank lines and # comments.
mapfile -t HOSTS < <(grep -vE '^\s*(#|$)' "$HOSTS_FILE" || true)

if [[ ${#HOSTS[@]} -eq 0 ]]; then
    echo "error: no hosts found in $HOSTS_FILE" >&2
    exit 1
fi

echo "Deploying curunir tag '$TAG' to ${#HOSTS[@]} host(s):"
printf '  %s\n' "${HOSTS[@]}"
echo

for host in "${HOSTS[@]}"; do
    echo "==> $host (CURUNIR_TAG=$TAG)"
    ssh "$host" "cd ~/curunir-deploy && CURUNIR_TAG='$TAG' docker compose pull curunir && CURUNIR_TAG='$TAG' docker compose up -d curunir"
    echo
done

echo "All hosts updated to CURUNIR_TAG=$TAG."
