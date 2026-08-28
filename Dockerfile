FROM python:3.12-slim

# Runtime user identity. These are build args, not constants, because
# docker-compose.yml bind-mounts host directories (./context, ./workspace,
# ./secrets) into the container. On Linux the container UID must own those
# paths or the agent cannot write memory, schedules, or the portfolio DB.
# Build with --build-arg APP_UID=$(id -u) --build-arg APP_GID=$(id -g) when
# the host user is not 1000. (Docker Desktop on macOS maps UIDs transparently;
# this only bites on Linux hosts.)
ARG APP_UID=1000
ARG APP_GID=1000

# Install system deps: ripgrep (grep tool), git, jq (web-search skill), curl,
# pandoc (markdown→PDF/HTML conversion for report attachments)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ripgrep git jq curl pandoc texlive-latex-recommended lmodern nodejs npm && \
    rm -rf /var/lib/apt/lists/*

# Install chub CLI (curated LLM-optimized API docs — used by skill-factory).
# Pinned: an unpinned `npm install -g` resolves to whatever the registry serves
# at build time, so two builds of the same commit ship different code.
RUN npm install -g @aisuite/chub@0.1.4

# Install gh CLI (used by github and git-contribute skills)
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      -o /usr/share/keyrings/githubcli-archive-keyring.gpg && \
    chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends gh && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# --require-hashes: requirements.txt is compiled from requirements.in with
# --generate-hashes, so every pin carries sha256 digests. A substituted,
# tampered, or yanked-and-replaced artifact fails the build instead of
# silently shipping. Regenerate with the command at the top of requirements.in.
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

# Install Chromium for shot-scraper (playwright skill). Installed to a shared
# path rather than root's ~/.cache/ms-playwright so the non-root runtime user
# below can still find the browser.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN shot-scraper install && chmod -R a+rX /opt/ms-playwright

COPY . .

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Drop root. The agent runs arbitrary tool-driven `bash`, so the blast radius
# of a prompt-injection or a malicious skill should not include the container's
# system paths. /app is chowned because the agent writes context/, workspace/,
# and the SQLite stores beneath it.
RUN groupadd -f -g "${APP_GID}" curunir && \
    useradd -u "${APP_UID}" -g "${APP_GID}" -m -s /bin/bash curunir && \
    mkdir -p /app/context /app/workspace && \
    chown -R "${APP_UID}:${APP_GID}" /app
USER curunir

EXPOSE 8765
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "run.py"]
