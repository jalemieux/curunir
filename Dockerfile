FROM python:3.12-slim

# Install system deps: ripgrep (grep tool), git, jq (web-search skill), curl,
# pandoc (markdown→PDF/HTML conversion for report attachments).
# texlive-xetex + fonts-dejavu give src.md2pdf its preferred xelatex engine with
# broad-Unicode coverage; texlive-latex-recommended + lmodern stay as the
# pdflatex fallback.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ripgrep git jq curl pandoc texlive-latex-recommended texlive-xetex lmodern fonts-dejavu nodejs npm && \
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
