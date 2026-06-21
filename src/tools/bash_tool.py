import subprocess

from src.config import AgentConfig

DEFAULT_TIMEOUT = 30
MAX_OUTPUT_CHARS = 30_000  # ~8k tokens — prevents a single curl from blowing the context


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
