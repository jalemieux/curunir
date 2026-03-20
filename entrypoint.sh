#!/bin/bash
set -e

# --- gog CLI setup ---
# Import OAuth token if a token file is mounted at /secrets/gog-token.json
GOG_CONFIG_DIR="${HOME}/.config/gogcli"
GOG_TOKEN_FILE="/secrets/gog-token.json"
GOG_CREDENTIALS_FILE="/secrets/gog-credentials.json"

if [ -f "$GOG_TOKEN_FILE" ]; then
    mkdir -p "$GOG_CONFIG_DIR"

    # Register credentials (OAuth client ID/secret) if provided
    if [ -f "$GOG_CREDENTIALS_FILE" ]; then
        gog auth credentials set "$GOG_CREDENTIALS_FILE"
    fi

    # File keyring needs a password — default to "curunir" if not set
    export GOG_KEYRING_PASSWORD="${GOG_KEYRING_PASSWORD:-curunir}"

    # Set file-based keyring (no macOS Keychain in Docker)
    gog config set keyring_backend file

    # Import the token
    gog auth tokens import "$GOG_TOKEN_FILE"

    echo "gog: token imported for $(jq -r .email "$GOG_TOKEN_FILE")"
else
    echo "gog: no token file at $GOG_TOKEN_FILE — email channel will not work"
fi

# --- Context repo sync ---
# Clone or pull the context repo if CONTEXT_SYNC_REMOTE is set
CONTEXT_DIR="${CONTEXT_DIR:-/app/context}"
if [ -n "${CONTEXT_SYNC_REMOTE:-}" ]; then
    CONTEXT_SYNC_BRANCH="${CONTEXT_SYNC_BRANCH:-main}"
    if [ -d "$CONTEXT_DIR/.git" ]; then
        echo "context-sync: pulling latest from $CONTEXT_SYNC_REMOTE ($CONTEXT_SYNC_BRANCH)"
        git -C "$CONTEXT_DIR" pull --ff-only origin "$CONTEXT_SYNC_BRANCH" || echo "context-sync: pull failed, continuing with local state"
    elif [ "$(ls -A "$CONTEXT_DIR" 2>/dev/null)" ]; then
        # Directory exists with files but no .git — init and pull
        echo "context-sync: existing files in $CONTEXT_DIR, initializing repo"
        git -C "$CONTEXT_DIR" init
        git -C "$CONTEXT_DIR" remote add origin "$CONTEXT_SYNC_REMOTE"
        git -C "$CONTEXT_DIR" fetch origin "$CONTEXT_SYNC_BRANCH"
        git -C "$CONTEXT_DIR" checkout -b "$CONTEXT_SYNC_BRANCH"
        git -C "$CONTEXT_DIR" add -A
        git -C "$CONTEXT_DIR" commit -m "import existing context" --allow-empty || true
    else
        echo "context-sync: cloning $CONTEXT_SYNC_REMOTE into $CONTEXT_DIR"
        git clone -b "$CONTEXT_SYNC_BRANCH" "$CONTEXT_SYNC_REMOTE" "$CONTEXT_DIR"
    fi
    # Configure git identity for runtime commits
    git -C "$CONTEXT_DIR" config user.email "curunir@bot"
    git -C "$CONTEXT_DIR" config user.name "curunir"
else
    echo "context-sync: CONTEXT_SYNC_REMOTE not set — local-only mode"
fi

# Hand off to CMD
exec "$@"
