#!/usr/bin/env bash
# Fan out an image-pull deploy of the curunir service to every entry listed in
# scripts/.deploy-hosts. Each line is one deploy target:
#
#   user@host                     # uses default ~/curunir-deploy
#   user@host  /path/to/deploy    # explicit deploy dir (for hosts running
#                                 # multiple curunir instances side-by-side)
#
# Blank lines and `#` comments are ignored. List the same host on multiple
# lines (with distinct deploy dirs) to update several instances on one box.
#
# Usage:
#   scripts/deploy.sh                 # pulls :latest on every target
#   scripts/deploy.sh sha-abc1234     # pins a specific tag across all targets
#   scripts/deploy.sh v1.2.3
#
# Only touches the `curunir` service. The portal lives on render.com and is
# updated separately — see docs/deployment.md.
#
# Each deploy dir is expected to contain the repo's docker-compose.yml, an
# .env, and the bind-mount subdirs (secrets/, workspace/, context/). See
# docs/deployment.md for the one-time host setup. Distinct deploy dirs give
# distinct compose project names automatically, so multiple instances on one
# host won't clobber each other's containers.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOSTS_FILE="$SCRIPT_DIR/.deploy-hosts"
TAG="${1:-latest}"
DEFAULT_DIR="~/curunir-deploy"

if [[ ! -f "$HOSTS_FILE" ]]; then
    echo "error: $HOSTS_FILE not found." >&2
    echo "Copy scripts/.deploy-hosts.example to scripts/.deploy-hosts and add one entry per line." >&2
    exit 1
fi

# Strip blank lines and # comments.
mapfile -t LINES < <(grep -vE '^\s*(#|$)' "$HOSTS_FILE" || true)

if [[ ${#LINES[@]} -eq 0 ]]; then
    echo "error: no entries found in $HOSTS_FILE" >&2
    exit 1
fi

# Parse each line into "host<TAB>dir". A line may be "host" or "host dir".
TARGETS=()
for line in "${LINES[@]}"; do
    read -r host dir _ <<<"$line"
    [[ -z "$host" ]] && continue
    TARGETS+=("$host"$'\t'"${dir:-$DEFAULT_DIR}")
done

echo "Deploying curunir tag '$TAG' to ${#TARGETS[@]} target(s):"
for t in "${TARGETS[@]}"; do
    printf '  %s  (%s)\n' "${t%%$'\t'*}" "${t##*$'\t'}"
done
echo

for t in "${TARGETS[@]}"; do
    host="${t%%$'\t'*}"
    dir="${t##*$'\t'}"
    echo "==> $host:$dir (CURUNIR_TAG=$TAG)"
    ssh "$host" "cd $dir && CURUNIR_TAG='$TAG' docker compose pull curunir && CURUNIR_TAG='$TAG' docker compose up -d curunir"
    echo
done

echo "All targets updated to CURUNIR_TAG=$TAG."
