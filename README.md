# Curunir

![Curunir](docs/curunir.jpg)

A configurable agent framework for building specialized digital assistants. Define an identity, add skills, connect channels — get a capable assistant tailored to your domain.

## Philosophy

Curunir is built on lessons learned from building multiple agentic loop-based assistants using various frontier models:

- **Models are best with bash.** Modern frontier models find the most agency when given shell access and file tools.
- **Skills are prompts.** Complex workflows are captured as markdown instructions the agent loads on demand. Markdown instructions that reference the base tools and any CLI tools available in the container.
- **Context rot is real.** Drift and noise degrade model performance. The system prompt stays minimal — identity, skill manifest, timestamp — and skills are loaded only when needed.
- **Memory is markdown.** Frontier models are very good at reading multi-layered structured markdown files. In our experimentation, this produced better results than sophisticated vector-based RAG pipelines.

## How It Works

```
Channels (CLI, Slack, Email)
        │
        ▼
    Message Queue
        │
        ▼
    Agent Loop ──→ 7 Tools (Glob, Grep, Read, Edit, Write, Bash, load_skill)
        │
        ▼
  Memory Extraction
        │
        ▼
    Reply routed back to origin channel
```

Messages arrive from any channel, enter a queue, and are processed sequentially. The agent loop calls an LLM with the conversation history and tool schemas. After each conversation, a separate pass extracts durable facts into the memory system.

One process. One profile. One message at a time.

## Quick Start

```bash
git clone https://github.com/your-org/curunir.git
cd curunir
cp .env.example .env        # add API keys
vim context/identity.md     # define your assistant's persona
python main.py              # starts CLI channel by default
```

Configure channels and model in `config.yaml`. Add skills by dropping a `SKILL.md` into `skills/your-skill/`.

## Project Status

Pre-implementation. The [design spec](docs/superpowers/specs/2026-03-13-valar-design.md) is complete. Code is next.

## License

TBD
