FROM python:3.12-slim

# Install system deps: ripgrep (grep tool), git, jq (web-search skill), curl,
# pandoc (markdown→PDF/HTML conversion for report attachments)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ripgrep git jq curl pandoc texlive-latex-recommended lmodern nodejs npm && \
    rm -rf /var/lib/apt/lists/*

# Install chub CLI (curated LLM-optimized API docs — used by skill-factory)
RUN npm install -g @aisuite/chub

# Install gh CLI (used by github and git-contribute skills)
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      -o /usr/share/keyrings/githubcli-archive-keyring.gpg && \
    chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends gh && \
    rm -rf /var/lib/apt/lists/*

# Install docker CLI (used by introspect skill to read container logs via the
# mounted /var/run/docker.sock — see docker-compose.yml)
RUN install -m 0755 -d /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/debian/gpg \
      -o /etc/apt/keyrings/docker.asc && \
    chmod a+r /etc/apt/keyrings/docker.asc && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo \"$VERSION_CODENAME\") stable" \
      > /etc/apt/sources.list.d/docker.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends docker-ce-cli && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium for shot-scraper (playwright skill)
RUN shot-scraper install

COPY . .

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8765
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "run.py"]
