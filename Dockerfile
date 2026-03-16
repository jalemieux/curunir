FROM python:3.12-slim

# Install system deps: ripgrep (grep tool), git, jq (web-search skill), curl,
# pandoc (markdown→PDF/HTML conversion for report attachments)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ripgrep git jq curl pandoc texlive-latex-base lmodern && \
    rm -rf /var/lib/apt/lists/*

# Install gog CLI (Google Workspace — Gmail, Calendar, Drive, etc.)
ARG GOG_VERSION=0.12.0
RUN curl -fsSL "https://github.com/steipete/gogcli/releases/download/v${GOG_VERSION}/gogcli_${GOG_VERSION}_linux_amd64.tar.gz" \
    | tar -xz -C /usr/local/bin gog && \
    chmod +x /usr/local/bin/gog

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "run.py"]
