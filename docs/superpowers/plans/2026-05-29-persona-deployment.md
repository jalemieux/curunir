# Persona Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a `CURUNIR_PERSONA=<name>` deployment mechanism where a `personas/<name>/` bundle curates the skill set, core tools, API-key documentation, and an extra system-prompt layer — leaving model, channels, memory, and scheduling unchanged.

**Architecture:** A new `src/persona.py` loads `personas/<name>/persona.yaml` at boot into a `Persona` dataclass. Its fields flow into existing seams: `skills` → an allowlist that filters `load_registry`; `tools` → `Agent(tools=...)`; expertise `.md` files → bootstrapped into `context/persona/` and appended to the system prompt; `keys` → a soft startup warning only. With `CURUNIR_PERSONA` unset, every code path falls back to today's exact behavior.

**Tech Stack:** Python 3.12, dataclasses, PyYAML (`yaml.safe_load`), pytest / pytest-asyncio.

**Related:** PR #282 (identity/behavior split — not merged to this branch; persona layer appends cleanly with or without `behavior.md`). PR #277 / issue #274 (finance agent — reframed as the first persona; its two new finance skills are #277's deliverable and join `personas/finance/persona.yaml` when they land).

---

### Task 1: Add PyYAML dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Confirm PyYAML is not already declared**

Run: `grep -i yaml requirements.txt`
Expected: no output (not declared).

- [ ] **Step 2: Add the dependency**

Append to `requirements.txt` (keep alphabetical placement if the file is sorted; otherwise append at end):

```
PyYAML>=6.0
```

- [ ] **Step 3: Verify it imports**

Run: `python -c "import yaml; print(yaml.__version__)"`
Expected: prints a version (e.g. `6.0.3`).

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "build: declare PyYAML dependency for persona manifests"
```

---

### Task 2: Persona manifest loader

**Files:**
- Create: `src/persona.py`
- Test: `tests/test_persona.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_persona.py`:

```python
# tests/test_persona.py
import logging
from pathlib import Path

import pytest

from src.persona import Persona, load_persona, warn_missing_keys


@pytest.fixture
def make_bundle(tmp_path, monkeypatch):
    """Create personas/<name>/persona.yaml under a temp PERSONAS_DIR."""
    personas_dir = tmp_path / "personas"
    monkeypatch.setattr("src.persona.PERSONAS_DIR", personas_dir)

    def _make(name: str, yaml_text: str) -> Path:
        bundle = personas_dir / name
        bundle.mkdir(parents=True)
        (bundle / "persona.yaml").write_text(yaml_text)
        return bundle

    return _make


def test_loads_full_manifest(make_bundle):
    make_bundle(
        "finance",
        "name: finance\n"
        "description: money helper\n"
        "skills:\n  - identity\n  - financial-analysis\n"
        "tools:\n  - read\n  - edit\n"
        "keys:\n  - FRED_API_KEY\n",
    )
    p = load_persona("finance")
    assert p == Persona(
        name="finance",
        description="money helper",
        skills=["identity", "financial-analysis"],
        tools=["read", "edit"],
        keys=["FRED_API_KEY"],
    )


def test_tools_omitted_means_none(make_bundle):
    make_bundle("min", "name: min\nskills:\n  - identity\n")
    p = load_persona("min")
    assert p.tools is None
    assert p.keys == []


def test_missing_bundle_raises_filenotfound(make_bundle):
    with pytest.raises(FileNotFoundError, match="Persona 'ghost' not found"):
        load_persona("ghost")


def test_malformed_yaml_raises_valueerror(make_bundle):
    make_bundle("bad", "skills: [unclosed\n")
    with pytest.raises(ValueError, match="Malformed persona manifest"):
        load_persona("bad")


def test_missing_skills_raises_valueerror(make_bundle):
    make_bundle("noskills", "name: noskills\n")
    with pytest.raises(ValueError, match="at least one skill"):
        load_persona("noskills")


def test_warn_missing_keys_returns_absent_names(make_bundle, caplog):
    p = Persona("f", "", ["identity"], None, ["FRED_API_KEY", "OTHER"])
    with caplog.at_level(logging.WARNING):
        missing = warn_missing_keys(p, {"OTHER": "set"})
    assert missing == ["FRED_API_KEY"]
    assert "FRED_API_KEY" in caplog.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_persona.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.persona'`.

- [ ] **Step 3: Write the implementation**

Create `src/persona.py`:

```python
# src/persona.py
"""Persona bundle loading — resolves personas/<name>/persona.yaml at boot.

A persona curates a deployment: an absolute skill allowlist, an optional core
tool allowlist, and a list of API-key names (documentation / soft warning
only — never a hard failure). Expertise prompt files live alongside in
personas/<name>/expertise/ and are bootstrapped into context/persona/
separately (see onboarding/bootstrap.py).
"""
import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

PERSONAS_DIR = Path("personas")


@dataclass(frozen=True)
class Persona:
    name: str
    description: str
    skills: list[str]
    tools: list[str] | None  # None = all default tools
    keys: list[str] = field(default_factory=list)


def persona_dir(name: str) -> Path:
    return PERSONAS_DIR / name


def load_persona(name: str) -> Persona:
    """Load and validate personas/<name>/persona.yaml.

    Raises FileNotFoundError if the bundle/manifest is missing, ValueError if
    the manifest is malformed or omits the required 'skills:' list.
    """
    manifest = persona_dir(name) / "persona.yaml"
    if not manifest.exists():
        raise FileNotFoundError(
            f"Persona '{name}' not found: expected manifest at {manifest}. "
            "Set CURUNIR_PERSONA to a directory under personas/."
        )
    try:
        data = yaml.safe_load(manifest.read_text()) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"Malformed persona manifest {manifest}: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"Persona manifest {manifest} must be a YAML mapping")

    skills = data.get("skills")
    if not isinstance(skills, list) or not skills:
        raise ValueError(
            f"Persona manifest {manifest} must list at least one skill "
            "under 'skills:'"
        )
    tools = data.get("tools")
    if tools is not None and not isinstance(tools, list):
        raise ValueError(
            f"Persona manifest {manifest} 'tools:' must be a list if present"
        )
    keys = data.get("keys") or []

    return Persona(
        name=str(data.get("name", name)),
        description=str(data.get("description", "")),
        skills=[str(s) for s in skills],
        tools=[str(t) for t in tools] if tools is not None else None,
        keys=[str(k) for k in keys],
    )


def warn_missing_keys(persona: Persona, environ) -> list[str]:
    """Log a soft warning for each declared key absent from the environment.

    Returns the list of missing key names (for testing). Never raises.
    """
    missing = [k for k in persona.keys if not environ.get(k)]
    for k in missing:
        logger.warning(
            "persona '%s' expects %s but it is unset in the environment",
            persona.name, k,
        )
    return missing
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_persona.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/persona.py tests/test_persona.py
git commit -m "feat: persona manifest loader"
```

---

### Task 3: Skill-registry allowlist filtering

**Files:**
- Modify: `src/skills.py` (`load_registry`, `build_skill_manifest`)
- Test: `tests/test_skills.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_skills.py`:

```python
def _write_skill(skills_dir, name):
    d = skills_dir / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: does {name}\n---\nbody\n"
    )


def test_allowlist_filters_registry(tmp_path):
    skills_dir = tmp_path / "skills"
    for n in ("identity", "financial-analysis", "comfyui"):
        _write_skill(skills_dir, n)
    reg = load_registry([skills_dir], allowlist={"identity", "financial-analysis"})
    assert set(reg) == {"identity", "financial-analysis"}


def test_no_allowlist_returns_all(tmp_path):
    skills_dir = tmp_path / "skills"
    for n in ("identity", "comfyui"):
        _write_skill(skills_dir, n)
    reg = load_registry([skills_dir])
    assert set(reg) == {"identity", "comfyui"}


def test_unknown_allowlisted_skill_warns_not_crashes(tmp_path, caplog):
    import logging
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "identity")
    with caplog.at_level(logging.WARNING):
        reg = load_registry([skills_dir], allowlist={"identity", "ghost"})
    assert set(reg) == {"identity"}
    assert "ghost" in caplog.text


def test_manifest_honors_allowlist(tmp_path):
    skills_dir = tmp_path / "skills"
    for n in ("identity", "comfyui"):
        _write_skill(skills_dir, n)
    manifest = build_skill_manifest([skills_dir], allowlist={"identity"})
    assert "identity" in manifest
    assert "comfyui" not in manifest
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_skills.py -k "allowlist or honors" -v`
Expected: FAIL — `load_registry() got an unexpected keyword argument 'allowlist'`.

- [ ] **Step 3: Modify `load_registry`**

In `src/skills.py`, change the signature and add filtering at the end. Replace:

```python
def load_registry(skill_dirs: list[Path]) -> dict[str, Skill]:
```

with:

```python
def load_registry(
    skill_dirs: list[Path], allowlist: set[str] | None = None
) -> dict[str, Skill]:
```

Then, immediately before the final `return registry`, insert:

```python
    if allowlist is not None:
        for unknown in sorted(allowlist - registry.keys()):
            logger.warning(
                "persona allowlist names unknown skill '%s'", unknown
            )
        registry = {k: v for k, v in registry.items() if k in allowlist}
```

- [ ] **Step 4: Modify `build_skill_manifest` to pass the allowlist through**

Replace:

```python
def build_skill_manifest(skill_dirs: list[Path]) -> str:
```

with:

```python
def build_skill_manifest(
    skill_dirs: list[Path], allowlist: set[str] | None = None
) -> str:
```

and replace the line `registry = load_registry(skill_dirs)` (inside `build_skill_manifest`) with:

```python
    registry = load_registry(skill_dirs, allowlist)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_skills.py -v`
Expected: PASS (existing tests + 4 new).

- [ ] **Step 6: Commit**

```bash
git add src/skills.py tests/test_skills.py
git commit -m "feat: optional skill allowlist for persona curation"
```

---

### Task 4: Config fields for persona

**Files:**
- Modify: `src/config.py` (`AgentConfig`)
- Test: `tests/test_config.py` (create if absent)

- [ ] **Step 1: Write the failing test**

Create or append `tests/test_config.py`:

```python
# tests/test_config.py
from pathlib import Path

from src.config import AgentConfig


def test_persona_fields_default_to_none_and_path():
    c = AgentConfig()
    assert c.persona is None
    assert c.skill_allowlist is None
    assert c.persona_prompt_dir == Path("./context/persona")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL — `AttributeError: 'AgentConfig' object has no attribute 'persona'`.

- [ ] **Step 3: Add the fields**

In `src/config.py`, inside the `AgentConfig` dataclass, after the `skill_dirs` field add:

```python
    persona: str | None = None
    skill_allowlist: list[str] | None = None
    persona_prompt_dir: Path = Path("./context/persona")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/config.py tests/test_config.py
git commit -m "feat: persona config fields on AgentConfig"
```

---

### Task 5: Persona prompt layer in the system prompt

**Files:**
- Modify: `src/agent/system_prompt.py` (`build_static_prompt`)
- Test: `tests/test_system_prompt.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_system_prompt.py`:

```python
def test_persona_prompt_files_appended_sorted(tmp_context, tmp_skills, agent_config):
    persona_dir = tmp_context / "persona"
    persona_dir.mkdir()
    (persona_dir / "20-guardrails.md").write_text("GUARDRAILS BLOCK")
    (persona_dir / "10-domain.md").write_text("DOMAIN BLOCK")
    agent_config.persona_prompt_dir = persona_dir

    result = build_static_prompt(agent_config)

    assert "You are a test assistant." in result
    # sorted by filename: 10-domain before 20-guardrails
    assert result.index("DOMAIN BLOCK") < result.index("GUARDRAILS BLOCK")


def test_missing_persona_dir_is_silently_skipped(tmp_context, tmp_skills, agent_config):
    agent_config.persona_prompt_dir = tmp_context / "no-persona"
    result = build_static_prompt(agent_config)
    assert "You are a test assistant." in result


def test_skill_allowlist_forwarded_to_manifest(tmp_context, tmp_skills, agent_config):
    for n in ("identity", "comfyui"):
        d = tmp_skills / n
        d.mkdir()
        (d / "SKILL.md").write_text(
            f"---\nname: {n}\ndescription: does {n}\n---\nbody\n"
        )
    agent_config.skill_allowlist = ["identity"]
    result = build_static_prompt(agent_config)
    assert "identity" in result
    assert "comfyui" not in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_system_prompt.py -k "persona or allowlist" -v`
Expected: FAIL (persona files not appended / allowlist not applied).

- [ ] **Step 3: Modify `build_static_prompt`**

In `src/agent/system_prompt.py`, replace the body after `identity = config.identity_file.read_text()` down to the `return` with:

```python
    identity = config.identity_file.read_text()
    parts = [identity]

    # Behavior layer (PR #282): operating defaults, optional. Appended if the
    # config carries a behavior_file and it exists. Guarded so this works on
    # branches where the #282 split has not landed yet.
    behavior_file = getattr(config, "behavior_file", None)
    if behavior_file is not None and behavior_file.exists():
        parts.append(behavior_file.read_text())

    # Persona expertise layer: domain .md files bootstrapped into
    # context/persona/. Sorted by filename so authors can order with numeric
    # prefixes (10-domain.md, 20-guardrails.md). Absent dir is skipped.
    if config.persona_prompt_dir.is_dir():
        for md in sorted(config.persona_prompt_dir.glob("*.md")):
            parts.append(md.read_text())

    manifest = build_skill_manifest(
        config.skill_dirs,
        set(config.skill_allowlist) if config.skill_allowlist else None,
    )
    if manifest:
        parts.append(manifest)
    return "\n\n".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_system_prompt.py -v`
Expected: PASS (existing + 3 new).

- [ ] **Step 5: Commit**

```bash
git add src/agent/system_prompt.py tests/test_system_prompt.py
git commit -m "feat: append persona expertise layer to system prompt"
```

---

### Task 6: Bootstrap persona expertise files into context/

**Files:**
- Modify: `onboarding/bootstrap.py` (add `PERSONAS_DIR`, `bootstrap_persona`)
- Test: `tests/test_bootstrap.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_bootstrap.py`:

```python
from onboarding.bootstrap import bootstrap_persona


@pytest.fixture
def persona_dirs(tmp_path, monkeypatch):
    personas_dir = tmp_path / "personas"
    context_dir = tmp_path / "context"
    monkeypatch.setattr("onboarding.bootstrap.PERSONAS_DIR", personas_dir)
    return personas_dir, context_dir


def test_persona_copies_expertise(persona_dirs):
    personas_dir, context_dir = persona_dirs
    exp = personas_dir / "finance" / "expertise"
    exp.mkdir(parents=True)
    (exp / "10-domain.md").write_text("DOMAIN")
    bootstrap_persona(context_dir, "finance")
    assert (context_dir / "persona" / "10-domain.md").read_text() == "DOMAIN"


def test_persona_does_not_overwrite(persona_dirs):
    personas_dir, context_dir = persona_dirs
    exp = personas_dir / "finance" / "expertise"
    exp.mkdir(parents=True)
    (exp / "10-domain.md").write_text("NEW")
    dest = context_dir / "persona"
    dest.mkdir(parents=True)
    (dest / "10-domain.md").write_text("EXISTING")
    bootstrap_persona(context_dir, "finance")
    assert (dest / "10-domain.md").read_text() == "EXISTING"


def test_persona_missing_expertise_is_noop(persona_dirs):
    personas_dir, context_dir = persona_dirs
    (personas_dir / "finance").mkdir(parents=True)
    bootstrap_persona(context_dir, "finance")  # no expertise/ dir
    assert not (context_dir / "persona").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_bootstrap.py -k persona -v`
Expected: FAIL — `ImportError: cannot import name 'bootstrap_persona'`.

- [ ] **Step 3: Add `PERSONAS_DIR` and `bootstrap_persona`**

In `onboarding/bootstrap.py`, after `DEFAULT_DIR = Path("context.default")` add:

```python
PERSONAS_DIR = Path("personas")
```

Then append this function at the end of the file:

```python
def bootstrap_persona(context_dir: Path, persona_name: str) -> None:
    """Copy personas/<name>/expertise/* into context_dir/persona/ on first run.

    Mirrors bootstrap_context's non-overwriting semantics: existing files are
    left untouched, so user edits survive restarts. Missing expertise/ dir is
    a silent no-op.
    """
    src_dir = PERSONAS_DIR / persona_name / "expertise"
    if not src_dir.is_dir():
        logger.debug("No expertise/ for persona %s, skipping", persona_name)
        return

    for src in sorted(src_dir.rglob("*")):
        if not src.is_file():
            continue
        relative = src.relative_to(src_dir)
        dest = context_dir / "persona" / relative
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        logger.info("Bootstrapped persona file %s", dest)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_bootstrap.py -v`
Expected: PASS (existing + 3 new).

- [ ] **Step 5: Commit**

```bash
git add onboarding/bootstrap.py tests/test_bootstrap.py
git commit -m "feat: bootstrap persona expertise files into context/persona/"
```

---

### Task 7: Wire persona selection into run.py

**Files:**
- Modify: `run.py` (`main`)

This task is integration glue (no unit test — `main()` is the async wiring
coroutine). Verify by booting with and without `CURUNIR_PERSONA`.

- [ ] **Step 1: Add imports**

Near the other `src.` imports at the top of `run.py`, add:

```python
from src.persona import load_persona, warn_missing_keys
from onboarding.bootstrap import bootstrap_persona
```

- [ ] **Step 2: Resolve the persona before building config**

In `main()`, immediately after the `vision_model = os.environ.get("VISION_MODEL")` line (just before `config = AgentConfig(`), insert:

```python
    persona_name = os.environ.get("CURUNIR_PERSONA", "").strip() or None
    persona = load_persona(persona_name) if persona_name else None
    if persona:
        logger.info(
            "Persona '%s' active: %d skills, tools=%s",
            persona.name, len(persona.skills),
            persona.tools if persona.tools is not None else "all-defaults",
        )
        bootstrap_persona(Path("./context"), persona_name)
        warn_missing_keys(persona, os.environ)
```

- [ ] **Step 3: Feed persona into the config**

In the `config = AgentConfig(` call, add these two lines among the existing
`**({...})` spreads:

```python
        **({"persona": persona_name} if persona_name else {}),
        **({"skill_allowlist": persona.skills} if persona else {}),
```

- [ ] **Step 4: Pass persona tools to the Agent**

Replace:

```python
    agent = Agent(config, usage_store=usage_store)
```

with:

```python
    agent = Agent(
        config,
        tools=persona.tools if persona else None,
        usage_store=usage_store,
    )
```

- [ ] **Step 5: Verify boot with no persona (regression)**

Run: `CURUNIR_PERSONA= python -c "import asyncio, run"`
Expected: imports cleanly, no error. (Full boot needs env/keys; this checks the import + wiring path is syntactically sound.)

Then run the suite:

Run: `pytest tests/ -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add run.py
git commit -m "feat: select persona at boot via CURUNIR_PERSONA"
```

---

### Task 8: Ship the finance persona bundle (validation case)

**Files:**
- Create: `personas/finance/persona.yaml`
- Create: `personas/finance/expertise/10-domain.md`
- Create: `personas/finance/expertise/20-guardrails.md`
- Create: `personas/finance/.env.finance.example`
- Create: `personas/finance/README.md`
- Test: `tests/test_persona.py` (add a bundle-on-disk parse test)

The two finance-specific skills from #277 (`thesis-management`,
`position-tracking`) do not exist yet — they are #277's deliverable. List only
skills that exist today; add the two new ones to `skills:` when #277 lands.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_persona.py`:

```python
def test_finance_bundle_parses_from_repo():
    # Loads the real personas/finance/ shipped in the repo (no monkeypatch).
    import importlib
    import src.persona as persona_mod
    importlib.reload(persona_mod)  # ensure PERSONAS_DIR is the real default
    p = persona_mod.load_persona("finance")
    assert p.name == "finance"
    assert "financial-analysis" in p.skills
    assert "investment-memo" in p.skills
    assert p.skills  # non-empty absolute allowlist
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_persona.py::test_finance_bundle_parses_from_repo -v`
Expected: FAIL — `FileNotFoundError: Persona 'finance' not found`.

- [ ] **Step 3: Create `personas/finance/persona.yaml`**

```yaml
name: finance
description: Local, private personal-finance assistant — capital allocation, position tracking, investment-thesis lifecycle, tax strategy.

# ABSOLUTE skill allowlist. Only these register for this deployment.
# Add thesis-management and position-tracking when PR #277 ships them.
skills:
  - identity
  - onboarding
  - financial-analysis
  - investment-memo
  - fred
  - sec-edgar
  - yfinance

# Core tools. Omit this block to inherit all default tools.
tools:
  - glob
  - grep
  - read
  - edit
  - write
  - bash
  - load_skill
  - web_fetch
  - delegate
  - schedule
  - attach

# Key NAMES only — documentation + soft startup warning. The operator
# supplies the values via the container environment / .env. See
# .env.finance.example and README.md.
keys:
  - FRED_API_KEY
```

- [ ] **Step 4: Create `personas/finance/expertise/10-domain.md`**

```markdown
## Domain: Personal Finance

You are a personal-finance assistant. Your areas of focus:

- **Capital allocation** — help the owner reason about position sizing,
  diversification, and opportunity cost across their accounts.
- **Position tracking** — keep an accurate picture of holdings, cost basis,
  and entry dates; reconcile against what the owner reports.
- **Investment-thesis lifecycle** — help create, revisit, and retire theses;
  surface the disconfirming evidence each thesis said to watch.
- **Tax strategy** — flag tax-aware framing (lot selection, holding periods,
  account placement) as considerations, not directives.

Always cite the numbers you used and show your arithmetic. Prefer concrete
figures over vague qualitative claims.
```

- [ ] **Step 5: Create `personas/finance/expertise/20-guardrails.md`**

```markdown
## Guardrails

- You are not a licensed financial advisor and do not give regulated
  investment advice. Frame outputs as analysis and options, not
  recommendations to buy or sell.
- Defer to the owner's judgment on any actual trade. Never place, simulate,
  or instruct trades.
- State assumptions explicitly. When data is stale or missing, say so rather
  than guessing.
- Keep the owner's financial details private — they live in local memory and
  must not be sent to third parties beyond the configured model and the
  explicit data tools the owner invokes.
```

- [ ] **Step 6: Create `personas/finance/.env.finance.example`**

```bash
# Finance persona — deployment environment template.
# Copy to .env and fill in. The persona declares which keys it needs in
# persona.yaml (keys:); this file is the operator's contract for providing them.

# --- Model (local + private by default; see README) ---
MODEL=ollama/qwen3:30b-a3b-instruct-q8_0
API_BASE=http://localhost:11434
MAX_HISTORY_CHARS=120000

# --- Persona selection ---
CURUNIR_PERSONA=finance

# --- Skill API keys (see persona.yaml `keys:`) ---
FRED_API_KEY=
```

- [ ] **Step 7: Create `personas/finance/README.md`**

```markdown
# Finance Persona

Local, private personal-finance assistant. Activated with
`CURUNIR_PERSONA=finance`.

## What it curates

- **Skills** — see `persona.yaml` `skills:` (analysis, memos, FRED, EDGAR,
  yfinance). `thesis-management` and `position-tracking` are added when
  PR #277 ships them.
- **Tools** — the standard default tool set (see `persona.yaml` `tools:`).
- **Prompt** — `expertise/10-domain.md` (focus areas) and
  `expertise/20-guardrails.md` (no regulated advice, privacy), layered on top
  of `context/identity.md` + `context/behavior.md`.

## Required keys

| Key | Used by | Notes |
|-----|---------|-------|
| `FRED_API_KEY` | `fred` skill | Free key from https://fred.stlouisfed.org/docs/api/api_key.html |

The model API key depends on your `MODEL`/`API_BASE`. The default config
points at a local Ollama (no third-party key needed).

## First boot

```bash
cp personas/finance/.env.finance.example .env   # fill in keys
CURUNIR_PERSONA=finance python run.py
```

On first run, `expertise/*.md` is copied into `context/persona/` (never
overwriting existing files) so you can tailor it locally.
```

- [ ] **Step 8: Run the test to verify it passes**

Run: `pytest tests/test_persona.py::test_finance_bundle_parses_from_repo -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add personas/finance tests/test_persona.py
git commit -m "feat: finance persona bundle (first persona on the framework)"
```

---

### Task 9: Documentation

**Files:**
- Modify: `CLAUDE.md` (Architecture section)
- Modify: `README.md` (new Personas section)
- Modify: `docs/architecture.md` (component table + ADR + changelog)
- Modify: `.env.example` (document `CURUNIR_PERSONA`)

- [ ] **Step 1: Document `CURUNIR_PERSONA` in `.env.example`**

Add near the top of `.env.example`:

```bash
# Persona selection (optional). When set to <name>, loads personas/<name>/
# persona.yaml: an absolute skill allowlist, optional core-tool allowlist, and
# an expertise prompt layer (bootstrapped into context/persona/). Unset = all
# skills, all default tools, no extra prompt layer (default behavior).
CURUNIR_PERSONA=
```

- [ ] **Step 2: Add a Personas subsection to `CLAUDE.md`**

In `CLAUDE.md`, after the `### Skills` section, add:

```markdown
### Personas (`personas/`, `src/persona.py`)

A persona is a deployment bundle selected at boot via `CURUNIR_PERSONA=<name>`.
`personas/<name>/persona.yaml` declares an **absolute** skill allowlist
(filters `load_registry`), an optional core-tool allowlist (drives
`Agent(tools=...)`), and key *names* for a soft startup warning. Expertise
`.md` files under `personas/<name>/expertise/` bootstrap into `context/persona/`
(non-overwriting) and append to the system prompt after identity/behavior.
Unset `CURUNIR_PERSONA` = today's behavior. API-key *values* are an operator
concern (env/.env), never declared in skills or code.
```

- [ ] **Step 3: Add a Personas section to `README.md`**

Add a new `## Personas` section (place it after the Skills section):

```markdown
## Personas

Run a domain-focused deployment by setting `CURUNIR_PERSONA=<name>`. A persona
(`personas/<name>/`) curates which skills register, which core tools are
available, and an extra prompt layer (domain expertise + guardrails) on top of
the base identity. The shipped example is `finance` — a local, private
personal-finance assistant. See `personas/finance/README.md`.

```bash
cp personas/finance/.env.finance.example .env
CURUNIR_PERSONA=finance python run.py
```
```

- [ ] **Step 4: Update `docs/architecture.md`**

Add `persona` / `personas/finance` to the relevant component table, then add
this ADR and a changelog entry:

```markdown
### ADR: Persona deployment via boot-time bundle selection

**Decision:** A persona is a `personas/<name>/` bundle selected by
`CURUNIR_PERSONA`, not a runtime-switchable per-session concept. It drives four
existing seams (skill allowlist, tool list, prompt layer, key docs) and is a
no-op when unset.

**Why:** One persona per deployment matches the "different persona deployment"
goal, keeps the agent loop and channels unchanged, and preserves backward
compatibility. Keys stay an ops/env concern to avoid coupling skill definitions
to deployment plumbing.

**Alternatives rejected:** runtime multi-persona (large agent-loop change);
per-skill `requires_keys` frontmatter (couples skills to deployment); reusing
`CURUNIR_DEFAULTS_DIR` (no first-class persona unit).
```

Add a dated changelog line at the bottom of `docs/architecture.md`:

```markdown
- 2026-05-29: Added persona deployment (`CURUNIR_PERSONA`, `personas/`,
  `src/persona.py`); finance is the first persona.
```

- [ ] **Step 5: Verify the suite still passes**

Run: `pytest tests/ -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md README.md docs/architecture.md .env.example
git commit -m "docs: document persona deployment"
```

---

## Self-Review Notes

- **Spec coverage:** Selection/back-compat → Task 7 + 4. Bundle/manifest →
  Tasks 2, 8. Skill allowlist (absolute) → Task 3. Tool allowlist (optional) →
  Tasks 2, 7. Keys (soft warning, ops-owned) → Tasks 2, 7, 8. Expertise layer
  → Tasks 5, 6, 8. Error handling (missing bundle/malformed yaml/unknown skill/
  missing dir) → Tasks 2, 3, 5. #277 reconciliation → Task 8. Testing → every
  task. Docs → Task 9.
- **#282 dependency:** Task 5 guards the `behavior_file` read with `getattr`,
  so the persona layer works whether or not #282 has merged into this branch.
- **Type consistency:** `Persona(name, description, skills, tools, keys)`,
  `load_persona(name)`, `warn_missing_keys(persona, environ)`,
  `load_registry(skill_dirs, allowlist)`, `build_skill_manifest(skill_dirs,
  allowlist)`, `bootstrap_persona(context_dir, persona_name)`,
  `config.persona / skill_allowlist / persona_prompt_dir` — used consistently
  across tasks.
```
