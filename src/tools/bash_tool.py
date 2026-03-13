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
