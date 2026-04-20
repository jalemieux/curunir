# Small-Model Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adapt Curunir to run on constrained local hardware by replacing the single-agent architecture with an orchestrator that delegates to specialized sub-agents, each running in a fresh, minimal context.

**Architecture:** An orchestrator agent holds conversation state and delegates tool work to named sub-agents defined in `context/agents.yaml`. Each sub-agent gets a minimal system prompt, a restricted tool set, and a low iteration cap. The orchestrator compacts delegation results into one-line summaries to keep its own history small.

**Tech Stack:** Python 3.12+, asyncio, LiteLLM, PyYAML (new dependency), pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-04-09-small-model-orchestrator-design.md`

---

## File Structure

| File | Responsibility |
|------|---------------|
| **Create:** `src/agent/agents_config.py` | Load and validate `agents.yaml`, provide `SubAgentDef` dataclass |
| **Create:** `context.default/agents.yaml` | Default sub-agent definitions (files, system, web, email, scheduler, memory) |
| **Modify:** `src/tools/delegate.py` | Accept `agent` parameter, look up config from `agents.yaml`, truncate results |
| **Modify:** `src/tools/schemas.py:219-247` | Update delegate schema with `agent` enum parameter |
| **Modify:** `src/agent/agent.py:83-109` | Add summary compaction for delegate exchanges in history |
| **Modify:** `src/agent/agent.py:128-134` | Accept optional `system_prompt_override` for sub-agents |
| **Modify:** `src/agent/system_prompt.py` | Support building orchestrator prompt from `agents.yaml` |
| **Modify:** `src/config.py` | Add `agents_file` path to `AgentConfig` |
| **Modify:** `run.py` | Pass agents config path, conditionally disable skills/memory extraction |
| **Modify:** `cli.py` | Add context usage indicator to prompt |
| **Create:** `tests/test_agents_config.py` | Tests for agents.yaml loading |
| **Modify:** `tests/test_delegate.py` | Tests for named agent delegation and result truncation |
| **Modify:** `tests/test_agent.py` | Tests for summary compaction |
| **Create:** `tests/test_orchestrator_prompt.py` | Tests for orchestrator prompt generation |

---

## Task 1: Add PyYAML dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add pyyaml to requirements.txt**

Add `pyyaml` to the requirements file:

```
pyyaml>=6.0
```

- [ ] **Step 2: Install and verify**

Run: `pip install pyyaml`
Expected: Successfully installed (or already satisfied)

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "feat: add pyyaml dependency for agents.yaml config"
```

---

## Task 2: Create `SubAgentDef` config loader (`src/agent/agents_config.py`)

**Files:**
- Create: `src/agent/agents_config.py`
- Create: `tests/test_agents_config.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_agents_config.py
"""Tests for src/agent/agents_config.py — agents.yaml loading."""

import pytest
from pathlib import Path
from src.agent.agents_config import SubAgentDef, load_agents_config


@pytest.fixture
def agents_yaml(tmp_path):
    """Write a minimal agents.yaml and return its path."""
    content = """\
files:
  description: "File operations"
  tools: [glob, grep, read, edit, write]
  system_prompt: >
    You are a file operations specialist. Complete the task below.
    Report what you did in under 100 words.
  max_iterations: 10

system:
  description: "Shell commands"
  tools: [bash]
  system_prompt: >
    You are a system operations specialist. Run commands to complete the task.
    Report output concisely.
  max_iterations: 10
"""
    path = tmp_path / "agents.yaml"
    path.write_text(content)
    return path


def test_load_returns_dict_of_sub_agent_defs(agents_yaml):
    agents = load_agents_config(agents_yaml)
    assert isinstance(agents, dict)
    assert "files" in agents
    assert "system" in agents
    assert isinstance(agents["files"], SubAgentDef)


def test_sub_agent_def_fields(agents_yaml):
    agents = load_agents_config(agents_yaml)
    files = agents["files"]
    assert files.name == "files"
    assert files.description == "File operations"
    assert files.tools == ["glob", "grep", "read", "edit", "write"]
    assert "file operations specialist" in files.system_prompt.lower()
    assert files.max_iterations == 10


def test_missing_file_returns_empty_dict(tmp_path):
    agents = load_agents_config(tmp_path / "nonexistent.yaml")
    assert agents == {}


def test_agent_names_method(agents_yaml):
    agents = load_agents_config(agents_yaml)
    names = sorted(agents.keys())
    assert names == ["files", "system"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agents_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.agent.agents_config'`

- [ ] **Step 3: Implement the module**

```python
# src/agent/agents_config.py
"""Load sub-agent definitions from agents.yaml."""

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SubAgentDef:
    name: str
    description: str
    tools: list[str]
    system_prompt: str
    max_iterations: int = 10


def load_agents_config(path: Path) -> dict[str, SubAgentDef]:
    """Load agents.yaml and return a dict of agent name -> SubAgentDef.

    Returns an empty dict if the file doesn't exist.
    """
    if not path.is_file():
        logger.debug("No agents config at %s", path)
        return {}

    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        logger.warning("agents.yaml is not a mapping, ignoring")
        return {}

    agents: dict[str, SubAgentDef] = {}
    for name, defn in raw.items():
        if not isinstance(defn, dict):
            continue
        agents[name] = SubAgentDef(
            name=name,
            description=defn.get("description", ""),
            tools=defn.get("tools", []),
            system_prompt=defn.get("system_prompt", ""),
            max_iterations=defn.get("max_iterations", 10),
        )

    logger.info("Loaded %d sub-agent definitions: %s", len(agents), ", ".join(agents))
    return agents
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agents_config.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent/agents_config.py tests/test_agents_config.py
git commit -m "feat: add agents.yaml config loader with SubAgentDef dataclass"
```

---

## Task 3: Create default `context.default/agents.yaml`

**Files:**
- Create: `context.default/agents.yaml`

- [ ] **Step 1: Write the default agents.yaml**

```yaml
# context.default/agents.yaml
# Sub-agent definitions for the small-model orchestrator.
# Each agent gets a fresh context per delegation — no carry-over between calls.

files:
  description: "File operations — read, edit, write, search"
  tools: [glob, grep, read, edit, write]
  system_prompt: >
    You are a file operations specialist. Complete the task below.
    Report what you did in under 100 words.
    Do not explain your reasoning. Just do the task and report the result.
  max_iterations: 10

system:
  description: "Shell commands and system management"
  tools: [bash]
  system_prompt: >
    You are a system operations specialist. Run commands to complete the task.
    Report output concisely.
  max_iterations: 10

web:
  description: "Fetch and process web content"
  tools: [web_fetch]
  system_prompt: >
    You are a web research specialist. Fetch the requested information
    and summarize it concisely.
  max_iterations: 5

email:
  description: "Read and send email"
  tools: [email_read, email_send]
  system_prompt: >
    You are an email specialist. Complete the email task and report what you did.
  max_iterations: 5

scheduler:
  description: "Manage recurring tasks"
  tools: [schedule]
  system_prompt: >
    You are a scheduling specialist. Create, modify, or report on scheduled tasks.
  max_iterations: 5

memory:
  description: "Store and recall information across sessions"
  tools: [read, write, glob]
  system_prompt: >
    You are a memory specialist. Read from or write to the memory directory
    at context/memory/. Report what you found or stored concisely.
  max_iterations: 5
```

- [ ] **Step 2: Verify bootstrap copies it**

Run: `python -c "from src.bootstrap import bootstrap_context; from pathlib import Path; import tempfile; d = Path(tempfile.mkdtemp()) / 'ctx'; bootstrap_context(d); print((d / 'agents.yaml').exists())"`
Expected: `True`

- [ ] **Step 3: Commit**

```bash
git add context.default/agents.yaml
git commit -m "feat: add default agents.yaml with six sub-agent definitions"
```

---

## Task 4: Add `agents_file` to `AgentConfig`

**Files:**
- Modify: `src/config.py:6-15`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_config.py`:

```python
def test_agents_file_default():
    from src.config import AgentConfig
    config = AgentConfig()
    assert config.agents_file == Path("./context/agents.yaml")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py::test_agents_file_default -v`
Expected: FAIL — `AttributeError: AgentConfig has no attribute 'agents_file'`

- [ ] **Step 3: Add the field to AgentConfig**

In `src/config.py`, add `agents_file` to the `AgentConfig` dataclass:

```python
@dataclass
class AgentConfig:
    model: str = "anthropic/claude-sonnet-4-20250514"
    api_base: str | None = None
    openrouter_provider: str | None = None
    max_iterations: int = 75
    max_history_chars: int = 250_000
    identity_file: Path = Path("./context/identity.md")
    context_dir: Path = Path("./context")
    skills_dir: Path = Path("./skills")
    agents_file: Path = Path("./context/agents.yaml")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: All pass

- [ ] **Step 5: Update conftest to include agents.yaml in tmp_context**

In `tests/conftest.py`, update the `tmp_context` fixture:

```python
@pytest.fixture
def tmp_context(tmp_path):
    """Create a temporary context directory with a minimal identity file."""
    identity = tmp_path / "identity.md"
    identity.write_text("You are a test assistant.")
    # Write a minimal agents.yaml for tests that need it
    agents = tmp_path / "agents.yaml"
    agents.write_text("files:\n  description: 'File ops'\n  tools: [read]\n  system_prompt: 'Do the task.'\n  max_iterations: 3\n")
    return tmp_path
```

- [ ] **Step 6: Run full test suite to check for regressions**

Run: `pytest tests/ -v`
Expected: All existing tests pass

- [ ] **Step 7: Commit**

```bash
git add src/config.py tests/conftest.py tests/test_config.py
git commit -m "feat: add agents_file path to AgentConfig"
```

---

## Task 5: Update delegate tool schema with `agent` parameter

**Files:**
- Modify: `src/tools/schemas.py:219-247`
- Modify: `tests/test_schemas.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_schemas.py`:

```python
def test_delegate_schema_has_agent_param():
    from src.tools.schemas import ALL_TOOL_SCHEMAS
    delegate = ALL_TOOL_SCHEMAS["delegate"]
    props = delegate["function"]["parameters"]["properties"]
    assert "agent" in props
    assert props["agent"]["type"] == "string"
    assert "required" in delegate["function"]["parameters"]
    assert "agent" in delegate["function"]["parameters"]["required"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_schemas.py::test_delegate_schema_has_agent_param -v`
Expected: FAIL — `KeyError: 'agent'`

- [ ] **Step 3: Update the delegate schema**

Replace the delegate schema in `src/tools/schemas.py` (the block starting at line 219):

```python
        {
            "type": "function",
            "function": {
                "name": "delegate",
                "description": (
                    "Delegate a task to a specialist agent. "
                    "Each specialist has specific tools and expertise. "
                    "Include all context the specialist needs in the task — "
                    "they have no memory of this conversation."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "agent": {
                            "type": "string",
                            "description": "Which specialist to delegate to.",
                        },
                        "task": {
                            "type": "string",
                            "description": "Concise task description with all necessary context.",
                        },
                    },
                    "required": ["agent", "task"],
                },
            },
        },
```

Note: The `image_paths` parameter is removed — sub-agents on small models won't handle images. The `agent` enum values will be injected at runtime (see Task 7).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_schemas.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add src/tools/schemas.py tests/test_schemas.py
git commit -m "feat: update delegate schema with agent parameter, remove image_paths"
```

---

## Task 6: Rewrite delegate executor for named agents

**Files:**
- Modify: `src/tools/delegate.py`
- Modify: `src/tools/dispatcher.py:34-45`
- Modify: `tests/test_delegate.py`

- [ ] **Step 1: Write failing tests**

Replace `tests/test_delegate.py` with:

```python
# tests/test_delegate.py
"""Tests for delegate tool — named agent delegation with result truncation."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path

from src.config import AgentConfig
from src.tools.delegate import exec_delegate

_AGENTS_YAML = """\
files:
  description: "File operations"
  tools: [glob, grep, read]
  system_prompt: "You are a file specialist. Do the task."
  max_iterations: 5

system:
  description: "Shell commands"
  tools: [bash]
  system_prompt: "You are a system specialist. Do the task."
  max_iterations: 3
"""


@pytest.fixture
def config_with_agents(tmp_path):
    identity = tmp_path / "identity.md"
    identity.write_text("Test assistant.")
    agents_file = tmp_path / "agents.yaml"
    agents_file.write_text(_AGENTS_YAML)
    return AgentConfig(
        identity_file=identity,
        context_dir=tmp_path,
        agents_file=agents_file,
    )


@pytest.mark.asyncio
async def test_delegate_to_named_agent(config_with_agents):
    with patch("src.tools.delegate.Agent") as MockAgent:
        mock_agent = AsyncMock()
        mock_agent.handle = AsyncMock(return_value="Found 3 files.")
        MockAgent.return_value = mock_agent

        result = await exec_delegate(
            {"agent": "files", "task": "List all .py files"},
            config_with_agents,
        )

        assert result == "Found 3 files."
        # Verify sub-agent was created with the right tools
        call_kwargs = MockAgent.call_args
        assert call_kwargs[1]["tools"] == ["glob", "grep", "read"]


@pytest.mark.asyncio
async def test_delegate_unknown_agent(config_with_agents):
    result = await exec_delegate(
        {"agent": "nonexistent", "task": "do something"},
        config_with_agents,
    )
    assert "unknown agent" in result.lower()


@pytest.mark.asyncio
async def test_delegate_no_agents_file(tmp_path):
    identity = tmp_path / "identity.md"
    identity.write_text("Test assistant.")
    config = AgentConfig(
        identity_file=identity,
        context_dir=tmp_path,
        agents_file=tmp_path / "nonexistent.yaml",
    )
    result = await exec_delegate(
        {"agent": "files", "task": "do something"},
        config,
    )
    assert "no agents" in result.lower() or "not configured" in result.lower()


@pytest.mark.asyncio
async def test_delegate_truncates_long_result(config_with_agents):
    long_result = "x" * 5000
    with patch("src.tools.delegate.Agent") as MockAgent:
        mock_agent = AsyncMock()
        mock_agent.handle = AsyncMock(return_value=long_result)
        MockAgent.return_value = mock_agent

        result = await exec_delegate(
            {"agent": "files", "task": "read a huge file"},
            config_with_agents,
        )

        assert len(result) <= 2048 + 50  # ~500 tokens ≈ 2000 chars, with margin
        assert result.endswith("... (truncated)")


@pytest.mark.asyncio
async def test_delegate_uses_agent_max_iterations(config_with_agents):
    with patch("src.tools.delegate.Agent") as MockAgent:
        mock_agent = AsyncMock()
        mock_agent.handle = AsyncMock(return_value="done")
        MockAgent.return_value = mock_agent

        await exec_delegate(
            {"agent": "system", "task": "run uptime"},
            config_with_agents,
        )

        call_kwargs = MockAgent.call_args
        config_arg = call_kwargs[0][0]  # first positional arg is config
        assert config_arg.max_iterations == 3


@pytest.mark.asyncio
async def test_delegate_uses_agent_system_prompt(config_with_agents):
    with patch("src.tools.delegate.Agent") as MockAgent:
        mock_agent = AsyncMock()
        mock_agent.handle = AsyncMock(return_value="done")
        MockAgent.return_value = mock_agent

        await exec_delegate(
            {"agent": "system", "task": "run uptime"},
            config_with_agents,
        )

        call_kwargs = MockAgent.call_args
        assert call_kwargs[1]["system_prompt_override"] == "You are a system specialist. Do the task."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_delegate.py -v`
Expected: FAIL — signature/behavior mismatches

- [ ] **Step 3: Rewrite the delegate executor**

Replace `src/tools/delegate.py`:

```python
# src/tools/delegate.py
"""Delegate tool — spawn a named sub-agent with restricted tools and context."""

import asyncio
import logging
from dataclasses import replace
from uuid import uuid4

from src.agent.agent import Agent
from src.agent.agents_config import load_agents_config
from src.config import AgentConfig

logger = logging.getLogger(__name__)

_TIMEOUT = 300
_MAX_RESULT_CHARS = 2048  # ~500 tokens safety net


async def exec_delegate(args: dict, config: AgentConfig, on_tool_call=None) -> str:
    """Spawn a named sub-agent and return its response."""
    agent_name = args.get("agent", "")
    task = args.get("task", "")
    if not agent_name:
        return "Error: 'agent' is required"
    if not task:
        return "Error: 'task' is required"

    agents = load_agents_config(config.agents_file)
    if not agents:
        return "Error: no agents configured (agents.yaml not found)"

    defn = agents.get(agent_name)
    if not defn:
        available = ", ".join(sorted(agents.keys()))
        return f"Error: unknown agent '{agent_name}'. Available: {available}"

    # Build a sub-agent config with the agent's iteration cap
    sub_config = replace(config, max_iterations=defn.max_iterations)

    sub_agent = Agent(
        sub_config,
        tools=defn.tools,
        system_prompt_override=defn.system_prompt,
    )
    session_id = str(uuid4())

    logger.info("Delegating to [%s] agent %s: %.80s", session_id[:8], agent_name, task)
    try:
        result = await asyncio.wait_for(
            sub_agent.handle(task, session_id, on_tool_call=on_tool_call),
            timeout=_TIMEOUT,
        )
        logger.info("Agent [%s] %s completed", session_id[:8], agent_name)
    except asyncio.TimeoutError:
        logger.warning("Agent [%s] %s timed out after %ds", session_id[:8], agent_name, _TIMEOUT)
        return f"Sub-agent '{agent_name}' timed out after {_TIMEOUT}s"
    except Exception as e:
        logger.error("Agent [%s] %s failed: %s", session_id[:8], agent_name, e)
        return f"Sub-agent '{agent_name}' error: {e}"

    # Truncate long results as a safety net
    if len(result) > _MAX_RESULT_CHARS:
        result = result[:_MAX_RESULT_CHARS] + "... (truncated)"

    return result
```

- [ ] **Step 4: Update Agent.__init__ to accept system_prompt_override**

In `src/agent/agent.py`, update the `__init__` method (line 129):

```python
class Agent:
    def __init__(self, config: AgentConfig, tools: list[str] | None = None,
                 system_prompt_override: str | None = None):
        self.config = config
        self.sessions: dict[str, list[dict]] = {}
        if system_prompt_override:
            self.static_prompt = system_prompt_override
        else:
            self.static_prompt = build_static_prompt(config)
        self.tools = tools  # None = all tools
        self._session_tools: dict[str, set[str]] = {}  # extra tools loaded by skills
```

- [ ] **Step 5: Update dispatcher to pass config to delegate**

The dispatcher at `src/tools/dispatcher.py:44-45` already passes `config` and `on_tool_call` to async executors. No change needed — the new `exec_delegate` signature matches.

Verify: `grep -n "async_executor" src/tools/dispatcher.py`
Expected: `return await async_executor(args, config, on_tool_call=on_tool_call)` — matches the new signature.

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_delegate.py -v`
Expected: All 7 tests PASS

- [ ] **Step 7: Run full test suite for regressions**

Run: `pytest tests/ -v`
Expected: All pass. Some existing delegate tests may need updating if they relied on `image_paths` or the old generic behavior — fix any that break.

- [ ] **Step 8: Commit**

```bash
git add src/tools/delegate.py src/agent/agent.py tests/test_delegate.py
git commit -m "feat: rewrite delegate tool for named sub-agents with result truncation"
```

---

## Task 7: Build orchestrator system prompt from agents.yaml

**Files:**
- Modify: `src/agent/system_prompt.py`
- Create: `tests/test_orchestrator_prompt.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_orchestrator_prompt.py
"""Tests for orchestrator prompt generation from agents.yaml."""

import pytest
from pathlib import Path
from src.agent.system_prompt import build_orchestrator_prompt


@pytest.fixture
def agents_yaml(tmp_path):
    content = """\
files:
  description: "File operations — read, edit, write, search"
  tools: [glob, grep, read, edit, write]
  system_prompt: "Do file stuff."
  max_iterations: 10

system:
  description: "Shell commands and system management"
  tools: [bash]
  system_prompt: "Do system stuff."
  max_iterations: 10
"""
    path = tmp_path / "agents.yaml"
    path.write_text(content)
    return path


def test_orchestrator_prompt_contains_agent_table(agents_yaml):
    prompt = build_orchestrator_prompt("Hal", agents_yaml)
    assert "files" in prompt
    assert "system" in prompt
    assert "File operations" in prompt
    assert "Shell commands" in prompt


def test_orchestrator_prompt_contains_name(agents_yaml):
    prompt = build_orchestrator_prompt("Hal", agents_yaml)
    assert "Hal" in prompt


def test_orchestrator_prompt_contains_rules(agents_yaml):
    prompt = build_orchestrator_prompt("Hal", agents_yaml)
    assert "delegate" in prompt.lower()


def test_orchestrator_prompt_missing_agents_file(tmp_path):
    prompt = build_orchestrator_prompt("Hal", tmp_path / "nope.yaml")
    # Should still produce a valid prompt, just with no specialists
    assert "Hal" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_orchestrator_prompt.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_orchestrator_prompt'`

- [ ] **Step 3: Implement build_orchestrator_prompt**

In `src/agent/system_prompt.py`, add the function:

```python
# src/agent/system_prompt.py
from pathlib import Path

from src.agent.agents_config import load_agents_config
from src.config import AgentConfig
from src.skills import build_skill_manifest


def build_static_prompt(config: AgentConfig) -> str:
    """Build the static portion of the system prompt (identity + skill manifest).

    Timestamp is appended per-call in Agent.handle().
    """
    if not config.identity_file.exists():
        raise FileNotFoundError(
            f"Identity file not found: {config.identity_file}. "
            "Curunir requires an identity file to start."
        )
    identity = config.identity_file.read_text()
    manifest = build_skill_manifest(config.skills_dir)
    parts = [identity]
    if manifest:
        parts.append(manifest)
    return "\n\n".join(parts)


def build_orchestrator_prompt(name: str, agents_file: Path) -> str:
    """Build a minimal orchestrator system prompt from agents.yaml.

    The orchestrator's job is to route tasks to specialists and
    answer simple questions directly. The prompt is kept small
    (~300 tokens) to fit within constrained context windows.
    """
    agents = load_agents_config(agents_file)

    # Build specialist table
    if agents:
        rows = []
        for agent_name, defn in agents.items():
            rows.append(f"| {agent_name} | {defn.description} |")
        table = "| Agent | Use for |\n|-------|--------|\n" + "\n".join(rows)
    else:
        table = "(No specialists configured)"

    return f"""You are {name}, a personal assistant running on local hardware.

You can answer simple questions directly. For tasks requiring tools, delegate to a specialist.

## Specialists
{table}

## Rules
- Delegate by calling the delegate tool with an agent name and a concise task description.
- Include all context the specialist needs in the task — they have no memory of this conversation.
- For multi-step tasks, delegate one step at a time and use each result to inform the next.
- After each delegation, summarize the result to the user in 1-2 sentences.
- If no tools are needed, respond directly."""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_orchestrator_prompt.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent/system_prompt.py tests/test_orchestrator_prompt.py
git commit -m "feat: add orchestrator prompt builder from agents.yaml"
```

---

## Task 8: Summary compaction for delegate exchanges in history

**Files:**
- Modify: `src/agent/agent.py:240-287`
- Modify: `tests/test_agent.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_agent.py`:

```python
@pytest.mark.asyncio
async def test_delegate_exchanges_compacted_in_history(agent_config):
    """After a delegate tool call, the tool_call + tool_result messages
    should be replaced with a [summary] message to save context space."""
    responses = [
        # First response: call delegate
        LLMResponse(
            text=None,
            tool_calls=[{
                "id": "tc1",
                "type": "function",
                "function": {
                    "name": "delegate",
                    "arguments": '{"agent": "system", "task": "run uptime"}',
                },
            }],
        ),
        # Second response: final answer after getting delegate result
        LLMResponse(text="The system has been up for 5 days.", tool_calls=None),
    ]
    with patch("src.agent.agent.call_llm", new_callable=AsyncMock, side_effect=responses):
        with patch("src.tools.dispatcher.execute_tool_call", new_callable=AsyncMock, return_value="uptime: 5 days"):
            agent = Agent(agent_config)
            await agent.handle("how long has this machine been running?", "s1")

    history = agent.sessions["s1"]
    # Should contain: user, [summary], assistant
    roles = [m["role"] for m in history]
    assert "tool" not in roles, "Raw tool messages should be compacted away"
    summaries = [m for m in history if m.get("role") == "assistant" and m.get("is_summary")]
    assert len(summaries) == 1
    assert "[system]" in summaries[0]["content"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_agent.py::test_delegate_exchanges_compacted_in_history -v`
Expected: FAIL — no compaction logic exists yet

- [ ] **Step 3: Implement summary compaction**

In `src/agent/agent.py`, add a helper function after `_trim_history`:

```python
def _compact_delegate_exchange(history: list[dict]) -> None:
    """Replace the most recent delegate tool_call + tool result with a summary.

    Looks for the pattern: assistant(tool_calls containing 'delegate') + tool result.
    Replaces both with a single summary message to save context.
    """
    if len(history) < 2:
        return

    # Find the last assistant message with tool_calls
    for i in range(len(history) - 1, -1, -1):
        msg = history[i]
        if msg["role"] != "assistant" or "tool_calls" not in msg:
            continue

        tool_calls = msg["tool_calls"]
        delegate_calls = [
            tc for tc in tool_calls
            if tc.get("function", {}).get("name") == "delegate"
        ]
        if not delegate_calls:
            continue

        # Collect the tool result messages that follow
        j = i + 1
        while j < len(history) and history[j]["role"] == "tool":
            j += 1

        # Extract agent name and result for the summary
        for tc in delegate_calls:
            args_str = tc.get("function", {}).get("arguments", "{}")
            try:
                args = json.loads(args_str)
            except json.JSONDecodeError:
                args = {}
            agent_name = args.get("agent", "delegate")

            # Find the matching tool result
            tc_id = tc.get("id")
            result_text = ""
            for k in range(i + 1, j):
                if history[k].get("tool_call_id") == tc_id:
                    result_text = history[k].get("content", "")
                    break

            # Truncate result for summary
            summary_text = result_text[:200]
            if len(result_text) > 200:
                summary_text += "..."

            summary = f"[{agent_name}] {summary_text}"

            # Replace the assistant+tool block with a summary
            del history[i:j]
            history.insert(i, {
                "role": "assistant",
                "content": summary,
                "is_summary": True,
            })
            return
```

Then in the `handle()` method, after tool execution completes (around line 285-287), add the compaction call. Specifically, after `_trim_history(history, ...)` inside the tool_calls branch:

```python
                _trim_history(history, max_chars=self.config.max_history_chars)
                # Compact delegate exchanges into summaries
                _compact_delegate_exchange(history)
                messages = [{"role": "system", "content": system_prompt}] + history
                continue
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_agent.py::test_delegate_exchanges_compacted_in_history -v`
Expected: PASS

- [ ] **Step 5: Run full agent tests for regressions**

Run: `pytest tests/test_agent.py -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add src/agent/agent.py tests/test_agent.py
git commit -m "feat: add summary compaction for delegate exchanges in history"
```

---

## Task 9: Wire orchestrator mode in run.py and update CLI welcome

**Files:**
- Modify: `run.py:297-357`
- Modify: `src/channels/ws.py:16-21,48-51`
- Modify: `.env.example`

- [ ] **Step 1: Add ORCHESTRATOR_MODE env var support**

In `run.py`, update the `main()` function. After building the config, check for orchestrator mode and configure accordingly:

```python
    bootstrap_context(config.context_dir)

    orchestrator_mode = os.environ.get("ORCHESTRATOR_MODE", "false").lower() == "true"

    if orchestrator_mode:
        from src.agent.system_prompt import build_orchestrator_prompt
        # Read agent name from identity file (first line, stripped of markdown)
        name_line = config.identity_file.read_text().splitlines()[0] if config.identity_file.exists() else "Assistant"
        agent_name = name_line.lstrip("#").strip()
        orchestrator_prompt = build_orchestrator_prompt(agent_name, config.agents_file)
        agent = Agent(config, tools=["delegate"], system_prompt_override=orchestrator_prompt)
        logger.info("Orchestrator mode: routing to agents defined in %s", config.agents_file)
    else:
        agent = Agent(config)

    in_queue = asyncio.Queue()
    out_queue = asyncio.Queue()
```

Also update the TaskGroup to conditionally include periodic_extraction:

```python
    async with asyncio.TaskGroup() as tg:
        for channel in channels.values():
            tg.create_task(channel.start())
        tg.create_task(route_outbound(out_queue, channels))
        tg.create_task(agent_worker(agent, in_queue, out_queue))
        if not orchestrator_mode:
            tg.create_task(periodic_extraction(agent, extraction_interval))
        tg.create_task(run_scheduler(agent))
```

- [ ] **Step 2: Pass orchestrator mode flag to WebSocket channel**

Update the `WebSocketChannel` constructor in `src/channels/ws.py` to accept a `local_mode` flag:

```python
class WebSocketChannel:
    def __init__(self, in_queue: asyncio.Queue, host: str = "0.0.0.0", port: int = 8765,
                 model: str = "", local_mode: bool = False):
        self.in_queue = in_queue
        self.host = host
        self.port = port
        self.model = model
        self.local_mode = local_mode
        self._connection: websockets.ServerConnection | None = None
```

Update the welcome message (line 50) to include `local_mode`:

```python
        if self.model:
            welcome = json.dumps({
                "content": "", "model": self.model, "final": False,
                "local_mode": self.local_mode,
            })
            await websocket.send(welcome)
```

In `run.py`, pass the flag when creating the channel:

```python
    ws = WebSocketChannel(in_queue, host=ws_host, port=ws_port, model=config.model,
                          local_mode=orchestrator_mode)
```

- [ ] **Step 3: Update CLI welcome message for local mode**

In `cli.py`, update the welcome message handler in `output_loop` (around line 89-91):

```python
                if "model" in data:
                    if data.get("local_mode"):
                        console.print(f"[bold]Curunir[/bold] [dim](local mode)[/dim]")
                        console.print("[dim]Tip: I work best with focused requests. Ask me to do something specific.[/dim]\n")
                    else:
                        console.print(f"[dim]model: {data['model']}[/dim]\n")
                    ready.set()
                    continue
```

- [ ] **Step 4: Add to .env.example**

Append to `.env.example`:

```
# Orchestrator mode — for small local models
# ORCHESTRATOR_MODE=true
```

- [ ] **Step 5: Manually test (smoke test)**

Run with orchestrator mode disabled (default): `python run.py` — verify it starts normally with the standard welcome. Then set `ORCHESTRATOR_MODE=true` and verify the "local mode" welcome appears.

- [ ] **Step 6: Commit**

```bash
git add run.py src/channels/ws.py cli.py .env.example
git commit -m "feat: wire orchestrator mode toggle with local-mode CLI welcome"
```

---

## Task 10: Inject agent enum into delegate schema at runtime

**Files:**
- Modify: `src/agent/agent.py:136-141`
- Modify: `tests/test_agent.py`

The delegate schema's `agent` parameter needs its `enum` populated with the actual agent names from `agents.yaml`. This happens when the Agent builds its tool schemas.

- [ ] **Step 1: Write failing test**

Add to `tests/test_agent.py`:

```python
@pytest.mark.asyncio
async def test_orchestrator_injects_agent_enum(tmp_path):
    """When tools=["delegate"], the delegate schema's agent param should
    have an enum populated from agents.yaml."""
    identity = tmp_path / "identity.md"
    identity.write_text("You are a test assistant.")
    agents_file = tmp_path / "agents.yaml"
    agents_file.write_text("files:\n  description: 'File ops'\n  tools: [read]\n  system_prompt: 'Do it.'\nsystem:\n  description: 'Shell'\n  tools: [bash]\n  system_prompt: 'Do it.'\n")

    config = AgentConfig(
        identity_file=identity,
        context_dir=tmp_path,
        agents_file=agents_file,
    )
    agent = Agent(config, tools=["delegate"])
    schemas = agent._get_tool_schemas()

    delegate_schema = next(s for s in schemas if s["function"]["name"] == "delegate")
    agent_prop = delegate_schema["function"]["parameters"]["properties"]["agent"]
    assert "enum" in agent_prop
    assert sorted(agent_prop["enum"]) == ["files", "system"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_agent.py::test_orchestrator_injects_agent_enum -v`
Expected: FAIL — no enum injection

- [ ] **Step 3: Implement enum injection**

In `src/agent/agent.py`, update `__init__` and `_get_tool_schemas`:

```python
class Agent:
    def __init__(self, config: AgentConfig, tools: list[str] | None = None,
                 system_prompt_override: str | None = None):
        self.config = config
        self.sessions: dict[str, list[dict]] = {}
        if system_prompt_override:
            self.static_prompt = system_prompt_override
        else:
            self.static_prompt = build_static_prompt(config)
        self.tools = tools  # None = all tools
        self._session_tools: dict[str, set[str]] = {}

        # If this agent uses delegate, load agent names for schema enum
        self._agent_names: list[str] | None = None
        if tools and "delegate" in tools:
            from src.agent.agents_config import load_agents_config
            agents = load_agents_config(config.agents_file)
            if agents:
                self._agent_names = sorted(agents.keys())

    def _get_tool_schemas(self, session_id: str | None = None) -> list[dict]:
        import copy
        base = get_tool_schemas(self.tools)
        if session_id and session_id in self._session_tools:
            extra = get_tool_schemas(list(self._session_tools[session_id]))
            base = base + extra

        # Inject agent enum into delegate schema
        if self._agent_names:
            base = copy.deepcopy(base)
            for schema in base:
                if schema["function"]["name"] == "delegate":
                    schema["function"]["parameters"]["properties"]["agent"]["enum"] = self._agent_names
                    break

        return base
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_agent.py::test_orchestrator_injects_agent_enum -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add src/agent/agent.py tests/test_agent.py
git commit -m "feat: inject agent names as enum in delegate schema at runtime"
```

---

## Task 11: Add context usage indicator to CLI prompt

**Files:**
- Modify: `src/channels/base.py:16-25`
- Modify: `src/channels/ws.py:84-101`
- Modify: `run.py:265-279`
- Modify: `cli.py`

- [ ] **Step 1: Add context_usage to OutgoingMessage**

In `src/channels/base.py`, add the field after `stats`:

```python
@dataclass
class OutgoingMessage:
    content: str
    channel: str
    session_id: str
    reply_address: dict
    tool_calls: list[str] | None = None
    final: bool = True
    attachments: list[dict] | None = None
    workflow: dict | None = None
    stats: dict | None = None
    context_usage: float | None = None  # 0.0 to 1.0
```

- [ ] **Step 2: Compute and send context_usage from agent_worker**

In `run.py`, in the `agent_worker` function, after `text = await handle_task` and before the `OutgoingMessage` constructor (around line 271), add:

```python
        # Compute context usage ratio for CLI indicator
        ctx_usage = None
        session_history = agent.sessions.get(msg.session_id, [])
        if session_history:
            from src.agent.agent import _estimate_chars
            used = _estimate_chars(session_history)
            ctx_usage = min(used / agent.config.max_history_chars, 1.0)
```

Then add `context_usage=ctx_usage` to the `OutgoingMessage(...)` call at line 271.

- [ ] **Step 3: Add context_usage to WebSocket send payload**

In `src/channels/ws.py`, update the `send` method's payload dict (line 89-96) to include context_usage:

```python
    async def send(self, msg: OutgoingMessage) -> None:
        if self._connection is None:
            logger.warning("No WebSocket client connected; dropping outgoing message")
            return

        payload: dict = {
            "content": msg.content,
            "tool_calls": msg.tool_calls,
            "final": msg.final,
            "attachments": msg.attachments if msg.attachments else None,
            "workflow": msg.workflow,
            "stats": msg.stats,
            "context_usage": msg.context_usage,
        }
        try:
            await self._connection.send(json.dumps(payload))
        except websockets.exceptions.ConnectionClosed:
            self._connection = None
            logger.warning("WebSocket connection closed while sending; message dropped")
```

- [ ] **Step 4: Add context bar helper and update CLI prompt**

In `cli.py`, add the helper function before the `run()` function:

```python
def _context_bar(usage: float | None) -> str:
    """Render a 5-block context usage bar, e.g. [ctx: ██░░░]."""
    if usage is None:
        return ""
    blocks = 5
    filled = round(usage * blocks)
    bar = "\u2588" * filled + "\u2591" * (blocks - filled)
    if filled >= 4:
        color = "yellow"
    else:
        color = "dim"
    return f"[{color}]\\[ctx: {bar}][/{color}] "
```

In `output_loop`, add a `nonlocal` variable to track usage. Before the `try` block (around line 71):

```python
        last_context_usage: float | None = None
```

In the message processing, after handling `final` (around line 163), capture usage:

```python
                ctx = data.get("context_usage")
                if ctx is not None:
                    nonlocal last_context_usage
                    last_context_usage = ctx
```

Update the input prompt (line 199) to include the context bar:

```python
                    ctx_prefix = _context_bar(last_context_usage)
                    line = await loop.run_in_executor(
                        None,
                        lambda: console.input(f"{ctx_prefix}[bold green]> [/bold green]"),
                    )
```

Note: `last_context_usage` needs to be shared between `output_loop` and the input loop. Make it a mutable container (e.g., `ctx_state = {"usage": None}`) accessible by both, since they're in the same `run()` scope.

- [ ] **Step 5: Manual test**

Start server and CLI, send a few messages, verify the context bar appears in the prompt and grows as the conversation continues.

- [ ] **Step 6: Commit**

```bash
git add src/channels/base.py src/channels/ws.py run.py cli.py
git commit -m "feat: add context usage indicator bar to CLI prompt"
```

---

## Task 12: Show delegation progress in CLI

**Files:**
- Modify: `run.py:29-53` (`_summarize_tool_call`)

- [ ] **Step 1: Update delegate summary format**

In `run.py`, update the delegate case in `_summarize_tool_call` to include the agent name:

```python
        case "delegate":
            agent_name = args.get("agent", "")
            task = args.get("task", "")
            if len(task) > 50:
                task = task[:47] + "..."
            return f"Delegate [{agent_name}]: {task}"
```

- [ ] **Step 2: Manual test**

Start server and CLI, trigger a delegation, verify the tool call display shows `Delegate [files]: ...` format.

- [ ] **Step 3: Commit**

```bash
git add run.py
git commit -m "feat: show agent name in delegation progress display"
```

---

## Task 13: Integration test — full orchestrator flow

**Files:**
- Create: `tests/test_orchestrator_integration.py`

- [ ] **Step 1: Write the integration test**

```python
# tests/test_orchestrator_integration.py
"""Integration test: orchestrator delegates to a named sub-agent and compacts history."""

import pytest
from unittest.mock import AsyncMock, patch

from src.agent.agent import Agent
from src.config import AgentConfig
from src.llm import LLMResponse


_AGENTS_YAML = """\
files:
  description: "File operations"
  tools: [read]
  system_prompt: "You are a file specialist."
  max_iterations: 3
"""


@pytest.fixture
def orchestrator(tmp_path):
    identity = tmp_path / "identity.md"
    identity.write_text("# TestBot")
    agents_file = tmp_path / "agents.yaml"
    agents_file.write_text(_AGENTS_YAML)

    from src.agent.system_prompt import build_orchestrator_prompt
    prompt = build_orchestrator_prompt("TestBot", agents_file)

    config = AgentConfig(
        identity_file=identity,
        context_dir=tmp_path,
        agents_file=agents_file,
        max_history_chars=16_000,
    )
    return Agent(config, tools=["delegate"], system_prompt_override=prompt)


@pytest.mark.asyncio
async def test_orchestrator_delegates_and_compacts(orchestrator):
    """Full flow: user asks → orchestrator delegates → result compacted → final answer."""
    # The orchestrator will make two LLM calls:
    # 1. Decides to delegate to files agent
    # 2. After getting the result, responds to user
    orchestrator_responses = [
        LLMResponse(
            text=None,
            tool_calls=[{
                "id": "tc1",
                "type": "function",
                "function": {
                    "name": "delegate",
                    "arguments": '{"agent": "files", "task": "Read /etc/hostname"}',
                },
            }],
        ),
        LLMResponse(text="The hostname is 'devbox'.", tool_calls=None),
    ]

    # The sub-agent spawned by delegate will also call the LLM
    sub_agent_responses = [
        LLMResponse(text="The file contains: devbox", tool_calls=None),
    ]

    call_count = 0

    async def mock_call_llm(model, messages, tools, **kwargs):
        nonlocal call_count
        call_count += 1
        # First two calls are orchestrator, third is sub-agent
        if call_count <= 2:
            return orchestrator_responses[call_count - 1]
        return sub_agent_responses[0]

    with patch("src.agent.agent.call_llm", side_effect=mock_call_llm):
        result = await orchestrator.handle("what is the hostname?", "sess1")

    assert result == "The hostname is 'devbox'."

    # History should be compacted: no raw tool messages
    history = orchestrator.sessions["sess1"]
    roles = [m["role"] for m in history]
    assert "tool" not in roles
```

- [ ] **Step 2: Run the test**

Run: `pytest tests/test_orchestrator_integration.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_orchestrator_integration.py
git commit -m "test: add orchestrator integration test for delegation and compaction"
```

---

## Task 14: Final cleanup and documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `.env.example`

- [ ] **Step 1: Update CLAUDE.md architecture section**

Add a subsection under Architecture describing the orchestrator mode:

```markdown
### Orchestrator Mode (Small-Model)

Set `ORCHESTRATOR_MODE=true` for constrained local hardware. The agent becomes an orchestrator that delegates to specialized sub-agents defined in `context/agents.yaml`. Each sub-agent runs in a fresh context with minimal overhead. Skills and automatic memory extraction are disabled. See the design spec at `docs/superpowers/specs/2026-04-09-small-model-orchestrator-design.md`.
```

- [ ] **Step 2: Update Key Environment Variables**

Add to the environment variables section:

```markdown
- `ORCHESTRATOR_MODE` — set to `true` for small-model orchestrator mode (delegates to sub-agents)
```

- [ ] **Step 3: Run full test suite one final time**

Run: `pytest tests/ -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md .env.example
git commit -m "docs: document orchestrator mode in CLAUDE.md and .env.example"
```
