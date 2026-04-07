FROM python:3.12-slim

# Install system deps: ripgrep (grep tool), git, jq (web-search skill), curl,
# pandoc (markdown→PDF/HTML conversion for report attachments)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ripgrep git jq curl pandoc texlive-latex-recommended lmodern nodejs npm && \
    rm -rf /var/lib/apt/lists/*

# Install chub CLI (curated LLM-optimized API docs — used by skill-factory)
RUN npm install -g @aisuite/chub

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
