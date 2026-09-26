import os
import subprocess
from pathlib import Path

from src.config import AgentConfig

DEFAULT_TIMEOUT = 30
MAX_OUTPUT_CHARS = 30_000  # ~8k tokens — prevents a single curl from blowing the context


def _script_env(config: AgentConfig) -> dict[str, str]:
    """Process env plus the agent's directories for skill scripts.

    ``CURUNIR_CONTEXT_DIR`` / ``CURUNIR_SHARED_DIR`` are the absolute
    counterparts of ``AgentConfig.path_vars``, so a script run from any cwd
    (``skills/balance-sheet/portfolio.py``, ``skills/crm/crm.py``,
    ``skills/webcam/snapshot.py``, ``skills/document-ingest/ingest.py``)
    defaults its store paths to this agent rather than to a literal
    ``context/``.
    """
    root = Path(config.repo_root)
    context_dir = Path(config.context_dir)
    shared_dir = Path(config.shared_dir)
    return {
        **os.environ,
        "CURUNIR_CONTEXT_DIR": str((root / context_dir).resolve()),
        "CURUNIR_SHARED_DIR": str((root / shared_dir).resolve()),
    }


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
            cwd=config.repo_root,
            env=_script_env(config),
        )
        output = result.stdout
        if result.stderr:
            output += result.stderr
        if not output:
            # A bare "" for a failed command is indistinguishable from a
            # successful no-output run — that hole let a silently-failing
            # verification command "pass" a gate (#413). Surface the exit code.
            if result.returncode != 0:
                return f"(no output; command exited with status {result.returncode})"
            return ""
        if len(output) > MAX_OUTPUT_CHARS:
            return output[:MAX_OUTPUT_CHARS] + f"\n\n... truncated ({len(output)} chars total, showing first {MAX_OUTPUT_CHARS})"
        return output
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {args.get('timeout', DEFAULT_TIMEOUT)} seconds."
    except Exception as e:
        return f"Error: {e}"
