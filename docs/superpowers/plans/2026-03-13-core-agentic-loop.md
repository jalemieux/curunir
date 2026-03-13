# Core Agentic Loop Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the core agentic loop for Curunir — the engine that receives messages, calls an LLM in a tool-calling loop, and returns responses.

**Architecture:** Sequential agent loop with in-memory sessions. LLM calls via LiteLLM, 7 tools dispatched through a registry. System prompt assembled from identity file + skill manifest + timestamp.

**Tech Stack:** Python 3.14, LiteLLM, pytest, python-dotenv

---

## Chunk 1: Scaffolding, Config, Skills, System Prompt

### Task 1: Project Scaffolding

**Files:**
- Create: `requirements.txt`
- Create: `src/__init__.py`
- Create: `src/agent/__init__.py`
- Create: `src/tools/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `context/identity.md`
- Create: `skills/` (directory only)

- [ ] **Step 1: Create requirements.txt**

```
litellm
python-dotenv
pytest
```

- [ ] **Step 2: Install dependencies**

Run: `.venv/bin/pip install -r requirements.txt`
Expected: Successfully installed packages

- [ ] **Step 3: Create package structure**

Create these empty `__init__.py` files:
- `src/__init__.py`
- `src/agent/__init__.py`
- `src/tools/__init__.py`
- `tests/__init__.py`

Each is an empty file (just `""`).

- [ ] **Step 4: Create test conftest.py (initial — no AgentConfig yet)**

```python
# tests/conftest.py
from pathlib import Path

import pytest


@pytest.fixture
def tmp_context(tmp_path):
    """Create a temporary context directory with a minimal identity file."""
    identity = tmp_path / "identity.md"
    identity.write_text("You are a test assistant.")
    return tmp_path


@pytest.fixture
def tmp_skills(tmp_path):
    """Create a temporary skills directory."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    return skills_dir
```

Note: The `agent_config` fixture depends on `AgentConfig` which is created in Task 2. It will be added to conftest in Task 2, Step 3.

- [ ] **Step 5: Create test identity file for manual testing**

Create `context/identity.md`:

```markdown
You are Curunir, a helpful assistant. You answer questions concisely and use your tools when needed.
```

- [ ] **Step 6: Create empty skills directory**

Run: `mkdir -p skills`

- [ ] **Step 7: Create pyproject.toml for pytest config**

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 8: Verify pytest runs with no tests**

Run: `.venv/bin/pytest -v`
Expected: "no tests ran" with exit code 5 (no tests collected)

- [ ] **Step 9: Commit**

```bash
git add requirements.txt pyproject.toml src/ tests/ context/identity.md
git commit -m "chore: scaffold project structure"
```

---

### Task 2: AgentConfig

**Files:**
- Create: `src/config.py`
- Create: `tests/test_config.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
from pathlib import Path

from src.config import AgentConfig


def test_default_config():
    config = AgentConfig()
    assert config.model == "anthropic/claude-sonnet-4-20250514"
    assert config.max_iterations == 15
    assert config.identity_file == Path("./context/identity.md")
    assert config.skills_dir == Path("./skills")


def test_custom_config():
    config = AgentConfig(model="openai/gpt-4o", max_iterations=5)
    assert config.model == "openai/gpt-4o"
    assert config.max_iterations == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.config'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/config.py
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AgentConfig:
    model: str = "anthropic/claude-sonnet-4-20250514"
    max_iterations: int = 15
    identity_file: Path = Path("./context/identity.md")
    skills_dir: Path = Path("./skills")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: 2 passed

- [ ] **Step 5: Add agent_config fixture to conftest.py**

Append to `tests/conftest.py`:

```python
from src.config import AgentConfig


@pytest.fixture
def agent_config(tmp_context, tmp_skills):
    """AgentConfig pointing at temporary directories."""
    return AgentConfig(
        identity_file=tmp_context / "identity.md",
        skills_dir=tmp_skills,
    )
```

- [ ] **Step 6: Commit**

```bash
git add src/config.py tests/test_config.py tests/conftest.py
git commit -m "feat: add AgentConfig dataclass"
```

---

### Task 3: Skills Scanner

**Files:**
- Create: `src/skills.py`
- Create: `tests/test_skills.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_skills.py
from pathlib import Path

from src.skills import build_skill_manifest, load_skill, parse_frontmatter


class TestParseFrontmatter:
    def test_valid_frontmatter(self):
        text = "---\nname: my-skill\ndescription: Do something\n---\n\n# Body"
        result = parse_frontmatter(text)
        assert result == {"name": "my-skill", "description": "Do something"}

    def test_no_frontmatter(self):
        result = parse_frontmatter("# Just a heading")
        assert result == {}

    def test_strips_quotes(self):
        text = '---\nname: "quoted-skill"\ndescription: \'single quoted\'\n---\n'
        result = parse_frontmatter(text)
        assert result == {"name": "quoted-skill", "description": "single quoted"}

    def test_colon_in_value(self):
        text = "---\nname: my-skill\ndescription: Use when: user asks\n---\n"
        result = parse_frontmatter(text)
        assert result["description"] == "Use when: user asks"


class TestBuildSkillManifest:
    def test_no_skills(self, tmp_path):
        assert build_skill_manifest(tmp_path) == ""

    def test_one_skill(self, tmp_path):
        skill_dir = tmp_path / "research"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: research\ndescription: When user asks to investigate\n---\n\n# Research"
        )
        result = build_skill_manifest(tmp_path)
        assert "| research | When user asks to investigate |" in result
        assert "## Available Skills" in result

    def test_multiple_skills_sorted(self, tmp_path):
        for name in ["zebra", "alpha"]:
            d = tmp_path / name
            d.mkdir()
            (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name} desc\n---\n")
        result = build_skill_manifest(tmp_path)
        lines = result.splitlines()
        skill_lines = [l for l in lines if l.startswith("| ") and "Skill" not in l]
        assert "alpha" in skill_lines[0]
        assert "zebra" in skill_lines[1]

    def test_nonexistent_dir(self, tmp_path):
        result = build_skill_manifest(tmp_path / "nonexistent")
        assert result == ""


class TestLoadSkill:
    def test_existing_skill(self, tmp_path):
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# My Skill\nInstructions here.")
        result = load_skill("my-skill", tmp_path)
        assert result == "# My Skill\nInstructions here."

    def test_missing_skill(self, tmp_path):
        result = load_skill("nonexistent", tmp_path)
        assert "not found" in result.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_skills.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

```python
# src/skills.py
from pathlib import Path


def build_skill_manifest(skills_dir: Path) -> str:
    """Scan skills dir, return markdown table of name + description."""
    if not skills_dir.exists():
        return ""

    skills = []
    for skill_file in sorted(skills_dir.glob("*/SKILL.md")):
        frontmatter = parse_frontmatter(skill_file.read_text())
        if "name" in frontmatter and "description" in frontmatter:
            skills.append((frontmatter["name"], frontmatter["description"]))

    if not skills:
        return ""

    lines = [
        "## Available Skills",
        "| Skill | When to Use |",
        "|-------|-------------|",
    ]
    for name, desc in skills:
        lines.append(f"| {name} | {desc} |")
    return "\n".join(lines)


def load_skill(name: str, skills_dir: Path) -> str:
    """Load full SKILL.md content by name."""
    path = skills_dir / name / "SKILL.md"
    if not path.exists():
        return f"Skill not found: {name}"
    return path.read_text()


def parse_frontmatter(text: str) -> dict:
    """Extract YAML frontmatter from markdown."""
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    fm = parts[1]
    result = {}
    for line in fm.strip().splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            result[key.strip()] = value.strip().strip("'\"")
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_skills.py -v`
Expected: All passed

- [ ] **Step 5: Commit**

```bash
git add src/skills.py tests/test_skills.py
git commit -m "feat: add skill scanner and manifest builder"
```

---

### Task 4: System Prompt Builder

**Files:**
- Create: `src/agent/system_prompt.py`
- Create: `tests/test_system_prompt.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_system_prompt.py
from pathlib import Path

import pytest

from src.agent.system_prompt import build_static_prompt
from src.config import AgentConfig


def test_builds_prompt_with_identity(tmp_context, tmp_skills, agent_config):
    result = build_static_prompt(agent_config)
    assert "You are a test assistant." in result


def test_includes_skill_manifest(tmp_context, tmp_skills, agent_config):
    skill_dir = tmp_skills / "research"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: research\ndescription: When investigating\n---\n"
    )
    result = build_static_prompt(agent_config)
    assert "research" in result
    assert "Available Skills" in result


def test_no_skills_section_when_empty(tmp_context, tmp_skills, agent_config):
    result = build_static_prompt(agent_config)
    assert "Available Skills" not in result


def test_missing_identity_file_raises(tmp_path, tmp_skills):
    config = AgentConfig(
        identity_file=tmp_path / "nonexistent.md",
        skills_dir=tmp_skills,
    )
    with pytest.raises(FileNotFoundError, match="identity file"):
        build_static_prompt(config)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_system_prompt.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

```python
# src/agent/system_prompt.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_system_prompt.py -v`
Expected: All passed

- [ ] **Step 5: Commit**

```bash
git add src/agent/system_prompt.py tests/test_system_prompt.py
git commit -m "feat: add system prompt builder"
```

---

## Chunk 2: Tool Schemas, Executors, Dispatcher

> **Prerequisite:** Chunk 1 must be complete. Tests in this chunk use `tmp_skills`, `tmp_context`, and `agent_config` fixtures from `tests/conftest.py` (created in Tasks 1-2).

### Task 5: Tool Schemas

**Files:**
- Create: `src/tools/schemas.py`
- Create: `tests/test_schemas.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_schemas.py
from src.tools.schemas import get_tool_schemas


def test_returns_seven_schemas():
    schemas = get_tool_schemas()
    assert len(schemas) == 7


def test_schema_format():
    for schema in get_tool_schemas():
        assert schema["type"] == "function"
        func = schema["function"]
        assert "name" in func
        assert "description" in func
        assert "parameters" in func
        assert func["parameters"]["type"] == "object"


def test_expected_tool_names():
    names = {s["function"]["name"] for s in get_tool_schemas()}
    assert names == {"glob", "grep", "read", "edit", "write", "bash", "load_skill"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_schemas.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

```python
# src/tools/schemas.py


def get_tool_schemas() -> list[dict]:
    """Return all 7 tool definitions in OpenAI function-calling format."""
    return [
        _glob_schema(),
        _grep_schema(),
        _read_schema(),
        _edit_schema(),
        _write_schema(),
        _bash_schema(),
        _load_skill_schema(),
    ]


def _glob_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find files matching a glob pattern. Returns newline-separated paths.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern (e.g. '**/*.py', 'src/*.md')",
                    },
                    "path": {
                        "type": "string",
                        "description": "Root directory to search from. Defaults to current directory.",
                    },
                },
                "required": ["pattern"],
            },
        },
    }


def _grep_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search file contents using regex via ripgrep. Returns matching lines.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regex pattern to search for.",
                    },
                    "path": {
                        "type": "string",
                        "description": "File or directory to search in. Defaults to current directory.",
                    },
                    "glob": {
                        "type": "string",
                        "description": "Filter files by glob pattern (e.g. '*.py').",
                    },
                    "output_mode": {
                        "type": "string",
                        "enum": ["content", "files_with_matches", "count"],
                        "description": "Output mode. Defaults to 'content'.",
                    },
                    "context": {
                        "type": "integer",
                        "description": "Lines of context around each match.",
                    },
                },
                "required": ["pattern"],
            },
        },
    }


def _read_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read a file's contents with line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to read.",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Line number to start reading from (1-based).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of lines to read.",
                    },
                },
                "required": ["file_path"],
            },
        },
    }


def _edit_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "edit",
            "description": "Replace exact string in a file. Fails if old_string is not found or not unique (unless replace_all is true).",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to edit.",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "Exact string to find and replace.",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "Replacement string.",
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace all occurrences. Defaults to false.",
                    },
                },
                "required": ["file_path", "old_string", "new_string"],
            },
        },
    }


def _write_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "write",
            "description": "Write content to a file. Creates parent directories if needed. Overwrites existing files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to write.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write to the file.",
                    },
                },
                "required": ["file_path", "content"],
            },
        },
    }


def _bash_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute a shell command and return stdout + stderr.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds. Defaults to 30.",
                    },
                },
                "required": ["command"],
            },
        },
    }


def _load_skill_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "load_skill",
            "description": "Load a skill's instructions by name. Returns the full SKILL.md content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the skill to load.",
                    },
                },
                "required": ["name"],
            },
        },
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_schemas.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/tools/schemas.py tests/test_schemas.py
git commit -m "feat: add tool schemas in OpenAI format"
```

---

### Task 6: File System Tool Executors

**Files:**
- Create: `src/tools/fs_tools.py`
- Create: `tests/test_fs_tools.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_fs_tools.py
from src.tools.fs_tools import exec_glob, exec_grep, exec_read, exec_edit, exec_write


class TestExecGlob:
    def test_finds_files(self, tmp_path, agent_config):
        (tmp_path / "a.py").write_text("hello")
        (tmp_path / "b.txt").write_text("world")
        result = exec_glob({"pattern": "*.py", "path": str(tmp_path)}, agent_config)
        assert "a.py" in result
        assert "b.txt" not in result

    def test_recursive(self, tmp_path, agent_config):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "deep.py").write_text("x")
        result = exec_glob({"pattern": "**/*.py", "path": str(tmp_path)}, agent_config)
        assert "deep.py" in result

    def test_no_matches(self, tmp_path, agent_config):
        result = exec_glob({"pattern": "*.xyz", "path": str(tmp_path)}, agent_config)
        assert result == ""

    def test_default_path(self, agent_config):
        result = exec_glob({"pattern": "*.py"}, agent_config)
        assert isinstance(result, str)


class TestExecRead:
    def test_reads_file_with_line_numbers(self, tmp_path, agent_config):
        f = tmp_path / "test.txt"
        f.write_text("line one\nline two\nline three\n")
        result = exec_read({"file_path": str(f)}, agent_config)
        assert "1\tline one" in result
        assert "2\tline two" in result
        assert "3\tline three" in result

    def test_offset_and_limit(self, tmp_path, agent_config):
        f = tmp_path / "test.txt"
        f.write_text("a\nb\nc\nd\ne\n")
        result = exec_read({"file_path": str(f), "offset": 2, "limit": 2}, agent_config)
        assert "1\t" not in result
        assert "2\tb" in result
        assert "3\tc" in result
        assert "4\t" not in result

    def test_file_not_found(self, agent_config):
        result = exec_read({"file_path": "/nonexistent/file.txt"}, agent_config)
        assert "error" in result.lower()


class TestExecEdit:
    def test_replaces_unique_string(self, tmp_path, agent_config):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        result = exec_edit(
            {"file_path": str(f), "old_string": "hello", "new_string": "goodbye"},
            agent_config,
        )
        assert f.read_text() == "goodbye world"
        assert "ok" in result.lower() or "success" in result.lower() or "replaced" in result.lower()

    def test_fails_if_not_found(self, tmp_path, agent_config):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        result = exec_edit(
            {"file_path": str(f), "old_string": "xyz", "new_string": "abc"},
            agent_config,
        )
        assert "not found" in result.lower()

    def test_fails_if_not_unique(self, tmp_path, agent_config):
        f = tmp_path / "test.txt"
        f.write_text("aa bb aa")
        result = exec_edit(
            {"file_path": str(f), "old_string": "aa", "new_string": "cc"},
            agent_config,
        )
        assert "not unique" in result.lower() or "multiple" in result.lower()
        assert f.read_text() == "aa bb aa"  # unchanged

    def test_replace_all(self, tmp_path, agent_config):
        f = tmp_path / "test.txt"
        f.write_text("aa bb aa")
        result = exec_edit(
            {"file_path": str(f), "old_string": "aa", "new_string": "cc", "replace_all": True},
            agent_config,
        )
        assert f.read_text() == "cc bb cc"


class TestExecWrite:
    def test_creates_file(self, tmp_path, agent_config):
        f = tmp_path / "new.txt"
        result = exec_write({"file_path": str(f), "content": "hello"}, agent_config)
        assert f.read_text() == "hello"

    def test_creates_parent_dirs(self, tmp_path, agent_config):
        f = tmp_path / "a" / "b" / "new.txt"
        exec_write({"file_path": str(f), "content": "deep"}, agent_config)
        assert f.read_text() == "deep"

    def test_overwrites_existing(self, tmp_path, agent_config):
        f = tmp_path / "existing.txt"
        f.write_text("old")
        exec_write({"file_path": str(f), "content": "new"}, agent_config)
        assert f.read_text() == "new"


class TestExecGrep:
    def test_finds_pattern(self, tmp_path, agent_config):
        (tmp_path / "a.py").write_text("def hello():\n    pass\n")
        result = exec_grep({"pattern": "def hello", "path": str(tmp_path)}, agent_config)
        assert "def hello" in result

    def test_no_matches(self, tmp_path, agent_config):
        (tmp_path / "a.py").write_text("nothing here\n")
        result = exec_grep({"pattern": "zzzzz", "path": str(tmp_path)}, agent_config)
        assert result == "" or "no matches" in result.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_fs_tools.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

```python
# src/tools/fs_tools.py
import glob as glob_module
import subprocess
from pathlib import Path

from src.config import AgentConfig


def exec_glob(args: dict, config: AgentConfig) -> str:
    """Find files matching a glob pattern."""
    try:
        pattern = args["pattern"]
        root = args.get("path", ".")
        matches = glob_module.glob(pattern, root_dir=root, recursive=True)
        return "\n".join(sorted(matches))
    except Exception as e:
        return f"Error: {e}"


def exec_grep(args: dict, config: AgentConfig) -> str:
    """Search file contents using ripgrep."""
    try:
        cmd = ["rg", "--no-heading"]
        pattern = args["pattern"]
        path = args.get("path", ".")

        if args.get("glob"):
            cmd.extend(["--glob", args["glob"]])

        output_mode = args.get("output_mode", "content")
        if output_mode == "files_with_matches":
            cmd.append("--files-with-matches")
        elif output_mode == "count":
            cmd.append("--count")

        if args.get("context"):
            cmd.extend(["-C", str(args["context"])])

        cmd.extend([pattern, path])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.stdout if result.stdout else ""
    except FileNotFoundError:
        return "Error: ripgrep (rg) is not installed."
    except Exception as e:
        return f"Error: {e}"


def exec_read(args: dict, config: AgentConfig) -> str:
    """Read a file with line numbers."""
    try:
        path = Path(args["file_path"])
        if not path.exists():
            return f"Error: File not found: {path}"

        lines = path.read_text().splitlines()
        offset = args.get("offset", 1)
        limit = args.get("limit", len(lines))

        # offset is 1-based
        start = max(0, offset - 1)
        end = start + limit
        selected = lines[start:end]

        numbered = []
        for i, line in enumerate(selected, start=start + 1):
            numbered.append(f"{i}\t{line}")
        return "\n".join(numbered)
    except Exception as e:
        return f"Error: {e}"


def exec_edit(args: dict, config: AgentConfig) -> str:
    """Replace exact string in a file."""
    try:
        path = Path(args["file_path"])
        if not path.exists():
            return f"Error: File not found: {path}"

        content = path.read_text()
        old = args["old_string"]
        new = args["new_string"]
        replace_all = args.get("replace_all", False)

        count = content.count(old)
        if count == 0:
            return f"Error: old_string not found in {path}"
        if count > 1 and not replace_all:
            return f"Error: old_string not unique in {path} (found {count} occurrences). Use replace_all=true to replace all."

        if replace_all:
            new_content = content.replace(old, new)
        else:
            new_content = content.replace(old, new, 1)

        path.write_text(new_content)
        return f"Replaced {count if replace_all else 1} occurrence(s) in {path}"
    except Exception as e:
        return f"Error: {e}"


def exec_write(args: dict, config: AgentConfig) -> str:
    """Write content to a file, creating parent dirs if needed."""
    try:
        path = Path(args["file_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args["content"])
        return f"Wrote {len(args['content'])} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_fs_tools.py -v`
Expected: All passed

- [ ] **Step 5: Commit**

```bash
git add src/tools/fs_tools.py tests/test_fs_tools.py
git commit -m "feat: add file system tool executors"
```

---

### Task 7: Bash Tool Executor

**Files:**
- Create: `src/tools/bash_tool.py`
- Create: `tests/test_bash_tool.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_bash_tool.py
from src.tools.bash_tool import exec_bash


class TestExecBash:
    def test_simple_command(self, agent_config):
        result = exec_bash({"command": "echo hello"}, agent_config)
        assert "hello" in result

    def test_captures_stderr(self, agent_config):
        result = exec_bash({"command": "echo err >&2"}, agent_config)
        assert "err" in result

    def test_timeout(self, agent_config):
        result = exec_bash({"command": "sleep 10", "timeout": 1}, agent_config)
        assert "timeout" in result.lower() or "timed out" in result.lower()

    def test_nonzero_exit(self, agent_config):
        result = exec_bash({"command": "exit 1"}, agent_config)
        assert isinstance(result, str)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_bash_tool.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

```python
# src/tools/bash_tool.py
import subprocess

from src.config import AgentConfig

DEFAULT_TIMEOUT = 30


def exec_bash(args: dict, config: AgentConfig) -> str:
    """Execute a shell command and return stdout + stderr."""
    try:
        timeout = args.get("timeout", DEFAULT_TIMEOUT)
        result = subprocess.run(
            args["command"],
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout
        if result.stderr:
            output += result.stderr
        return output if output else ""
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {args.get('timeout', DEFAULT_TIMEOUT)} seconds."
    except Exception as e:
        return f"Error: {e}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_bash_tool.py -v`
Expected: All passed

- [ ] **Step 5: Commit**

```bash
git add src/tools/bash_tool.py tests/test_bash_tool.py
git commit -m "feat: add bash tool executor"
```

---

### Task 8: Skill Tool Executor

**Files:**
- Create: `src/tools/skill_tool.py`
- Create: `tests/test_skill_tool.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_skill_tool.py
from src.tools.skill_tool import exec_load_skill


class TestExecLoadSkill:
    def test_loads_existing_skill(self, tmp_skills, agent_config):
        skill_dir = tmp_skills / "research"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Research\nDo research things.")
        result = exec_load_skill({"name": "research"}, agent_config)
        assert "# Research" in result

    def test_missing_skill(self, agent_config):
        result = exec_load_skill({"name": "nonexistent"}, agent_config)
        assert "not found" in result.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_skill_tool.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

```python
# src/tools/skill_tool.py
from src.config import AgentConfig
from src.skills import load_skill


def exec_load_skill(args: dict, config: AgentConfig) -> str:
    """Load a skill's instructions by name."""
    return load_skill(args["name"], config.skills_dir)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_skill_tool.py -v`
Expected: All passed

- [ ] **Step 5: Commit**

```bash
git add src/tools/skill_tool.py tests/test_skill_tool.py
git commit -m "feat: add skill tool executor"
```

---

### Task 9: Tool Dispatcher

**Files:**
- Create: `src/tools/dispatcher.py`
- Create: `tests/test_dispatcher.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_dispatcher.py
from src.tools.dispatcher import execute_tool_call


class TestExecuteToolCall:
    def test_dispatches_glob(self, tmp_path, agent_config):
        (tmp_path / "x.py").write_text("hi")
        result = execute_tool_call("glob", {"pattern": "*.py", "path": str(tmp_path)}, agent_config)
        assert "x.py" in result

    def test_dispatches_read(self, tmp_path, agent_config):
        f = tmp_path / "test.txt"
        f.write_text("content")
        result = execute_tool_call("read", {"file_path": str(f)}, agent_config)
        assert "content" in result

    def test_dispatches_write(self, tmp_path, agent_config):
        f = tmp_path / "out.txt"
        execute_tool_call("write", {"file_path": str(f), "content": "data"}, agent_config)
        assert f.read_text() == "data"

    def test_dispatches_bash(self, agent_config):
        result = execute_tool_call("bash", {"command": "echo dispatch_test"}, agent_config)
        assert "dispatch_test" in result

    def test_unknown_tool(self, agent_config):
        result = execute_tool_call("nonexistent", {}, agent_config)
        assert "unknown tool" in result.lower()

    def test_case_insensitive(self, agent_config):
        result = execute_tool_call("Bash", {"command": "echo case_test"}, agent_config)
        assert "case_test" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_dispatcher.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

```python
# src/tools/dispatcher.py
from src.config import AgentConfig
from src.tools.bash_tool import exec_bash
from src.tools.fs_tools import exec_edit, exec_glob, exec_grep, exec_read, exec_write
from src.tools.skill_tool import exec_load_skill

EXECUTORS = {
    "glob": exec_glob,
    "grep": exec_grep,
    "read": exec_read,
    "edit": exec_edit,
    "write": exec_write,
    "bash": exec_bash,
    "load_skill": exec_load_skill,
}


def execute_tool_call(name: str, args: dict, config: AgentConfig) -> str:
    """Dispatch a tool call to the appropriate executor."""
    executor = EXECUTORS.get(name.lower())
    if not executor:
        return f"Unknown tool: {name}"
    return executor(args, config)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_dispatcher.py -v`
Expected: All passed

- [ ] **Step 5: Run all tests to verify nothing is broken**

Run: `.venv/bin/pytest -v`
Expected: 30+ tests pass (config, skills, system_prompt, schemas, fs_tools, bash, skill_tool, dispatcher)

- [ ] **Step 6: Commit**

```bash
git add src/tools/dispatcher.py tests/test_dispatcher.py
git commit -m "feat: add tool dispatcher"
```

---

## Chunk 3: LLM Wrapper, Agent Loop, Test Script

### Task 10: LLM Wrapper

**Files:**
- Create: `src/llm.py`
- Create: `tests/test_llm.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_llm.py
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.llm import LLMResponse, call_llm


@pytest.mark.asyncio
async def test_text_response():
    mock_message = MagicMock()
    mock_message.content = "Hello back"
    mock_message.tool_calls = None

    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=mock_message)]

    with patch("src.llm.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=mock_response)
        result = await call_llm("test-model", [{"role": "user", "content": "hi"}], [])

    assert isinstance(result, LLMResponse)
    assert result.text == "Hello back"
    assert result.tool_calls is None


@pytest.mark.asyncio
async def test_tool_call_response():
    mock_tc = MagicMock()
    mock_tc.id = "call_123"
    mock_tc.function.name = "bash"
    mock_tc.function.arguments = '{"command": "echo hi"}'

    mock_message = MagicMock()
    mock_message.content = None
    mock_message.tool_calls = [mock_tc]

    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=mock_message)]

    with patch("src.llm.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=mock_response)
        result = await call_llm("test-model", [], [])

    assert result.text is None
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["id"] == "call_123"
    assert result.tool_calls[0]["function"]["name"] == "bash"


@pytest.mark.asyncio
async def test_both_text_and_tool_calls():
    mock_tc = MagicMock()
    mock_tc.id = "call_456"
    mock_tc.function.name = "read"
    mock_tc.function.arguments = '{"file_path": "test.py"}'

    mock_message = MagicMock()
    mock_message.content = "Let me check that file"
    mock_message.tool_calls = [mock_tc]

    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=mock_message)]

    with patch("src.llm.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=mock_response)
        result = await call_llm("test-model", [], [])

    assert result.text == "Let me check that file"
    assert len(result.tool_calls) == 1
```

- [ ] **Step 2: Install pytest-asyncio**

Run: `.venv/bin/pip install pytest-asyncio`

Add `pytest-asyncio` to `requirements.txt`.

Add to `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_llm.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Write implementation**

```python
# src/llm.py
from dataclasses import dataclass

import litellm


@dataclass
class LLMResponse:
    text: str | None
    tool_calls: list[dict] | None


async def call_llm(model: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
    """Call LLM via LiteLLM, return normalized response."""
    kwargs = {
        "model": model,
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools

    response = await litellm.acompletion(**kwargs)
    choice = response.choices[0].message

    text = choice.content if choice.content else None

    tool_calls = None
    if choice.tool_calls:
        tool_calls = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in choice.tool_calls
        ]

    return LLMResponse(text=text, tool_calls=tool_calls)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_llm.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add src/llm.py tests/test_llm.py requirements.txt pyproject.toml
git commit -m "feat: add LLM wrapper with LiteLLM"
```

---

### Task 11: Agent Loop

**Files:**
- Create: `src/agent/agent.py`
- Create: `tests/test_agent.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_agent.py
import json
from unittest.mock import AsyncMock, patch

import pytest

from src.agent.agent import Agent
from src.llm import LLMResponse


@pytest.fixture
def agent(agent_config):
    return Agent(agent_config)


class TestAgentHandle:
    async def test_returns_text_response(self, agent):
        mock_response = LLMResponse(text="Hello!", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response):
            result = await agent.handle("hi", "test-session")
        assert result == "Hello!"

    async def test_session_persistence(self, agent):
        response1 = LLMResponse(text="First", tool_calls=None)
        response2 = LLMResponse(text="Second", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, side_effect=[response1, response2]):
            await agent.handle("msg1", "s1")
            await agent.handle("msg2", "s1")
        history = agent.sessions["s1"]
        assert len(history) == 4  # user, assistant, user, assistant
        assert history[0]["content"] == "msg1"
        assert history[1]["content"] == "First"

    async def test_separate_sessions(self, agent):
        mock_response = LLMResponse(text="Reply", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response):
            await agent.handle("msg1", "session-a")
            await agent.handle("msg2", "session-b")
        assert len(agent.sessions["session-a"]) == 2
        assert len(agent.sessions["session-b"]) == 2

    async def test_executes_tool_calls(self, agent):
        tool_response = LLMResponse(
            text=None,
            tool_calls=[{
                "id": "call_1",
                "type": "function",
                "function": {"name": "bash", "arguments": json.dumps({"command": "echo tool_test"})},
            }],
        )
        text_response = LLMResponse(text="Done!", tool_calls=None)

        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, side_effect=[tool_response, text_response]):
            result = await agent.handle("run something", "s1")

        assert result == "Done!"
        history = agent.sessions["s1"]
        # user, assistant(tool_calls), tool, assistant(text)
        assert len(history) == 4
        assert history[1].get("tool_calls") is not None
        assert history[2]["role"] == "tool"
        assert "tool_test" in history[2]["content"]

    async def test_handles_combined_text_and_tool_calls(self, agent):
        combined = LLMResponse(
            text="Let me check",
            tool_calls=[{
                "id": "call_2",
                "type": "function",
                "function": {"name": "bash", "arguments": json.dumps({"command": "echo combined"})},
            }],
        )
        final = LLMResponse(text="All done", tool_calls=None)

        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, side_effect=[combined, final]):
            result = await agent.handle("check", "s1")

        assert result == "All done"
        # The combined response should have content preserved
        assert agent.sessions["s1"][1].get("content") == "Let me check"
        assert agent.sessions["s1"][1].get("tool_calls") is not None

    async def test_empty_response_returns_error(self, agent):
        empty = LLMResponse(text=None, tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=empty):
            result = await agent.handle("hello", "s1")
        assert "error" in result.lower()

    async def test_max_iterations(self, agent_config):
        agent_config.max_iterations = 2
        agent = Agent(agent_config)

        tool_response = LLMResponse(
            text=None,
            tool_calls=[{
                "id": "call_loop",
                "type": "function",
                "function": {"name": "bash", "arguments": json.dumps({"command": "echo loop"})},
            }],
        )
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=tool_response):
            result = await agent.handle("loop forever", "s1")
        assert "iteration limit" in result.lower()


class TestAgentInit:
    def test_loads_identity(self, agent):
        assert "test assistant" in agent.static_prompt.lower()

    def test_missing_identity_raises(self, tmp_path, tmp_skills):
        from src.config import AgentConfig
        config = AgentConfig(
            identity_file=tmp_path / "nonexistent.md",
            skills_dir=tmp_skills,
        )
        with pytest.raises(FileNotFoundError):
            Agent(config)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_agent.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

```python
# src/agent/agent.py
import asyncio
import json
from datetime import datetime

from src.agent.system_prompt import build_static_prompt
from src.config import AgentConfig
from src.llm import call_llm
from src.tools.dispatcher import execute_tool_call
from src.tools.schemas import get_tool_schemas


class Agent:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.sessions: dict[str, list[dict]] = {}
        self.static_prompt = build_static_prompt(config)

    async def handle(self, message: str, session_id: str) -> str:
        """Process a message and return the agent's response."""
        history = self.sessions.setdefault(session_id, [])
        history.append({"role": "user", "content": message})

        system_prompt = self.static_prompt + f"\n\nCurrent time: {datetime.now().isoformat()}"
        messages = [{"role": "system", "content": system_prompt}] + history

        for _ in range(self.config.max_iterations):
            response = await call_llm(self.config.model, messages, get_tool_schemas())

            if response.tool_calls:
                assistant_msg: dict = {"role": "assistant", "tool_calls": response.tool_calls}
                if response.text:
                    assistant_msg["content"] = response.text
                history.append(assistant_msg)

                for tool_call in response.tool_calls:
                    result = await asyncio.to_thread(
                        execute_tool_call,
                        tool_call["function"]["name"],
                        json.loads(tool_call["function"]["arguments"]),
                        self.config,
                    )
                    history.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": result,
                    })

                messages = [{"role": "system", "content": system_prompt}] + history
                continue

            if response.text:
                history.append({"role": "assistant", "content": response.text})
                return response.text

            history.append({"role": "assistant", "content": ""})
            return "Error: LLM returned empty response."

        return "Iteration limit reached."
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_agent.py -v`
Expected: All passed

- [ ] **Step 5: Run full test suite**

Run: `.venv/bin/pytest -v`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add src/agent/agent.py tests/test_agent.py
git commit -m "feat: add agent loop with session management"
```

---

### Task 12: Test Script and Final Wiring

**Files:**
- Create: `run.py`
- Create: `.env.example`

- [ ] **Step 1: Create run.py**

```python
# run.py
import asyncio

from dotenv import load_dotenv

from src.agent.agent import Agent
from src.config import AgentConfig


async def main():
    load_dotenv()
    config = AgentConfig()
    agent = Agent(config)

    print("Curunir agent ready. Type 'quit' to exit.\n")
    while True:
        try:
            user_input = input("> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if user_input.strip().lower() == "quit":
            break
        response = await agent.handle(user_input, session_id="cli")
        print(f"\n{response}\n")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Create .env.example**

```
# API key for your LLM provider
# ANTHROPIC_API_KEY=sk-...
# OPENAI_API_KEY=sk-...
```

- [ ] **Step 3: Verify run.py imports work**

Run: `.venv/bin/python -c "from src.agent.agent import Agent; from src.config import AgentConfig; print('imports ok')"`
Expected: `imports ok`

- [ ] **Step 4: Run full test suite one final time**

Run: `.venv/bin/pytest -v`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add run.py .env.example
git commit -m "feat: add test script and env example"
```
