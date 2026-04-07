#!/bin/bash
set -e

# --- Context repo sync ---
# Clone or pull the context repo if CONTEXT_SYNC_REMOTE is set
CONTEXT_DIR="${CONTEXT_DIR:-/app/context}"
if [ -n "${CONTEXT_SYNC_REMOTE:-}" ]; then
    # Configure git credentials for HTTPS push (uses GH_TOKEN env var)
    if [ -n "${GH_TOKEN:-}" ]; then
        git config --global credential.helper "!f() { echo username=x-access-token; echo password=${GH_TOKEN}; }; f"
    fi
    CONTEXT_SYNC_BRANCH="${CONTEXT_SYNC_BRANCH:-main}"
    # Configure git identity early (needed for init+commit path)
    git config --global user.email "curunir@bot"
    git config --global user.name "curunir"
    # Remove stale submodule .git pointer (file, not dir) left over from COPY
    if [ -f "$CONTEXT_DIR/.git" ]; then
        echo "context-sync: removing stale submodule .git pointer"
        rm "$CONTEXT_DIR/.git"
    fi
    if [ -d "$CONTEXT_DIR/.git" ]; then
        echo "context-sync: pulling latest from $CONTEXT_SYNC_REMOTE ($CONTEXT_SYNC_BRANCH)"
        git -C "$CONTEXT_DIR" pull --rebase origin "$CONTEXT_SYNC_BRANCH" || echo "context-sync: pull failed, continuing with local state"
    elif [ "$(ls -A "$CONTEXT_DIR" 2>/dev/null)" ]; then
        # Directory exists with files but no .git — init and pull
        echo "context-sync: existing files in $CONTEXT_DIR, initializing repo"
        git -C "$CONTEXT_DIR" init -b "$CONTEXT_SYNC_BRANCH"
        git -C "$CONTEXT_DIR" remote add origin "$CONTEXT_SYNC_REMOTE"
        git -C "$CONTEXT_DIR" fetch origin "$CONTEXT_SYNC_BRANCH"
        git -C "$CONTEXT_DIR" add -A
        git -C "$CONTEXT_DIR" commit -m "import existing context" --allow-empty || true
    else
        echo "context-sync: cloning $CONTEXT_SYNC_REMOTE into $CONTEXT_DIR"
        git clone -b "$CONTEXT_SYNC_BRANCH" "$CONTEXT_SYNC_REMOTE" "$CONTEXT_DIR"
    fi
else
    echo "context-sync: CONTEXT_SYNC_REMOTE not set — local-only mode"
fi

# Hand off to CMD
exec "$@"
