#!/bin/bash
set -e

# Pull context directory from a remote machine before starting curunir.
# Usage: ./sync-context.sh user@server:/path/to/context/
#
# Examples:
#   ./sync-context.sh pi@homelab:~/curunir/context/
#   ./sync-context.sh deploy@prod:/opt/curunir/context/

if [ -z "$1" ]; then
    echo "Usage: $0 <source>"
    echo "  source: rsync-compatible remote path (e.g. user@host:/path/to/context/)"
    exit 1
fi

SOURCE="$1"
DEST="${2:-./context/}"

echo "Syncing context from $SOURCE to $DEST ..."
rsync -az --delete "$SOURCE" "$DEST"
echo "Done."
