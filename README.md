# Curunir — *the man of skill*

<img src="docs/curunir2.png" alt="Curunir" style="border-radius: 8px;" />

A configurable agent framework for building specialized digital assistants. Define an identity, add skills, connect channels — get a capable assistant tailored to your domain.

## Philosophy

Curunir is built on lessons learned from building multiple agentic loop-based assistants using various frontier models:

- **Models are best with bash.** Modern frontier models find the most agency when given shell access and file tools.
- **Skills are prompts.** Complex workflows are captured as markdown instructions the agent loads on demand. Markdown instructions that reference the base tools and any CLI tools available in the container.
- **Context rot is real.** Drift and noise degrade model performance. The system prompt stays minimal — identity, skill manifest, timestamp — and skills are loaded only when needed.
- **Memory is markdown.** Frontier models are very good at reading multi-layered structured markdown files. In our experimentation, this produced better results than sophisticated vector-based RAG pipelines.

## Architecture

```
  Channels              Core                    LLM
  ────────          ──────────              ──────────
  CLI ──────┐
  Email ────┤       ┌──────────┐        ┌──────────────┐
  Slack* ───┴──►  Queue  ──►  Agent Loop  ◄──►  LiteLLM  │
                    └──────────┘        └──────────────┘
                         │
                         ▼
                 ┌───────────────┐
                 │     Tools     │
                 │───────────────│
                 │ glob    read  │
                 │ grep    edit  │
                 │ write   bash  │
                 │ load_skill    │
                 │ web_fetch     │
                 │ delegate      │
                 │ attach*       │
                 └───────────────┘
                 * opt-in, loaded by skills
                         │
                         ▼
                 ┌───────────────┐
                 │   Memory      │
                 │  Extractor    │
                 └───────────────┘

  * planned
```

Messages arrive from any channel, enter a queue, and are processed by the agent loop. The agent calls an LLM with conversation history and tool schemas, iterating up to 15 tool-calling rounds per turn. Replies are routed back to the originating channel.

Dashed nodes are planned but not yet implemented. The memory extractor runs post-session (on `/clear` or `/new`, EOF, or a periodic timer) to extract durable facts into `context/memory/`.

## Project Structure

```
curunir/
├── run.py                  # Entry point — wires channels, queues, agent
├── src/
│   ├── agent/              # Core agent loop and system prompt builder
│   ├── channels/           # Channel implementations (CLI, Email) and router
│   ├── tools/              # Tool schemas, dispatch, and executors
│   ├── config.py           # AgentConfig dataclass
│   ├── llm.py              # LLM interface (LiteLLM)
│   ├── memory_extractor.py # Post-session memory extraction
│   └── skills.py           # Skill manifest and loader
├── skills/                 # Drop-in skills (each a dir with SKILL.md)
│   └── extract-learnings/  # Extract durable knowledge from comms
├── context/
│   ├── identity.md         # Assistant persona and instructions
│   └── memory/             # Persistent markdown memory store
└── Dockerfile              # Container with Python 3.12, ripgrep, git
```

## Quick Start

### Local

```bash
git clone https://github.com/jalemieux/curunir.git
cd curunir
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # add your API key
vim context/identity.md     # define your assistant's persona
python run.py               # starts CLI channel
```

### Docker

```bash
docker compose up --build
```

#### Email Channel (Gmail)

The email channel uses [gog](https://github.com/steipete/gogcli) to access Gmail via OAuth. Setup has two parts: a one-time credential setup and a token that needs periodic renewal.

##### One-Time Setup: OAuth Credentials

1. Go to [Google Cloud Console → Credentials](https://console.cloud.google.com/apis/credentials)
2. **Create Credentials → OAuth client ID → Desktop app** → Download the JSON file
3. Install the [gog CLI](https://github.com/steipete/gogcli) and register the credentials:
   ```bash
   gog auth credentials set <downloaded-credentials.json>
   ```
4. Authorize the bot's Gmail account (opens a browser for OAuth consent):
   ```bash
   gog auth add <bot-email@gmail.com>
   ```
5. Place the credentials and token in `secrets/`:
   ```bash
   mkdir -p secrets
   cp <downloaded-credentials.json> secrets/gog-credentials.json
   gog auth tokens export <bot-email@gmail.com> --out secrets/gog-token.json
   ```
6. Enable the email channel in your `.env`:
   ```bash
   EMAIL_ENABLED=true
   GOG_ACCOUNT=<bot-email@gmail.com>
   EMAIL_ALLOWED_SENDERS=allowed-sender@example.com
   ```

> **Note:** The email used in all `gog` commands must match — it's the Gmail account the bot sends and receives from, not your personal email.

##### Renewing the Token

OAuth tokens expire periodically. When the email channel stops working, re-export:

```bash
gog auth tokens export <bot-email@gmail.com> --out secrets/gog-token.json
docker compose restart
```

If the refresh token itself has expired (`gog` gives a "Secret not found in keyring" error), re-authorize first:

```bash
gog auth add <bot-email@gmail.com>
gog auth tokens export <bot-email@gmail.com> --out secrets/gog-token.json
docker compose restart
```

The `secrets/` directory is mounted read-only into the container at `/secrets`. The entrypoint script automatically imports both files into the `gog` CLI configuration on startup.

| Variable | Default | Description |
|----------|---------|-------------|
| `EMAIL_ENABLED` | `false` | Enable the email channel |
| `GOG_ACCOUNT` | — | Gmail address to poll and send from |
| `EMAIL_ALLOWED_SENDERS` | — | Comma-separated list of allowed sender addresses (empty = allow all) |
| `EMAIL_POLL_INTERVAL` | `60` | Seconds between inbox polls |
| `EMAIL_PROCESSED_LABEL` | `agent/processed` | Gmail label applied to processed threads |

## Adding Skills

Drop a directory into `skills/` with a `SKILL.md` file:

```
skills/
└── my-skill/
    └── SKILL.md
```

The `SKILL.md` uses YAML frontmatter for discovery:

```yaml
---
name: my-skill
description: When to use this skill
---

# My Skill

Instructions the agent follows when it loads this skill...
```

Skills appear in the agent's system prompt as a manifest table. The agent calls `load_skill` to fetch full instructions on demand.

### Skill-Requested Tools

Skills can declare opt-in tools that are only available when the skill is loaded. Add a `tools` field to the frontmatter:

```yaml
---
name: deep-research
description: Research a topic in depth
tools: attach
---
```

When the agent loads this skill via `load_skill`, the listed tools are added to the agent's tool set for the remainder of the session. This keeps the default tool set lean while allowing skills to unlock capabilities they need.

**Available opt-in tools:**

| Tool | Description |
|------|-------------|
| `attach` | Attach a file to the agent's response. Delivered as an email attachment, CLI file path, etc. depending on channel. |

## Configuration

Configuration is handled via `src/config.py`:

| Setting | Default | Description |
|---------|---------|-------------|
| `model` | `anthropic/claude-sonnet-4-20250514` | LLM model (any LiteLLM-supported model) |
| `max_iterations` | `15` | Max tool-calling rounds per turn |
| `identity_file` | `./context/identity.md` | Path to persona file |
| `context_dir` | `./context` | Path to context directory (memory, etc.) |
| `skills_dir` | `./skills` | Path to skills directory |

API keys are set via environment variables (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc.).

## License

TBD
