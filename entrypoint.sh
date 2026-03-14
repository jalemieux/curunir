#!/bin/bash
set -e

# --- gog CLI setup ---
# Import OAuth token if a token file is mounted at /secrets/gog-token.json
GOG_CONFIG_DIR="${HOME}/.config/gogcli"
GOG_TOKEN_FILE="/secrets/gog-token.json"
GOG_CREDENTIALS_FILE="/secrets/gog-credentials.json"

if [ -f "$GOG_TOKEN_FILE" ]; then
    mkdir -p "$GOG_CONFIG_DIR"

    # Copy credentials (OAuth client ID/secret) if provided
    if [ -f "$GOG_CREDENTIALS_FILE" ]; then
        cp "$GOG_CREDENTIALS_FILE" "$GOG_CONFIG_DIR/credentials.json"
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

# Hand off to CMD
exec "$@"
