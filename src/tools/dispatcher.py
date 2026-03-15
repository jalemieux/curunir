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

ASYNC_EXECUTORS: set[str] = {"delegate"}


def is_async_executor(name: str) -> bool:
    """Check if a tool executor is async (needs await, not to_thread)."""
    return name.lower() in ASYNC_EXECUTORS


def _get_async_executor(name: str):
    """Lazily import async executors to avoid circular dependencies."""
    if name == "delegate":
        from src.tools.delegate import exec_delegate
        return exec_delegate
    return None


async def execute_tool_call_async(name: str, args: dict, config: AgentConfig) -> str:
    """Dispatch an async tool call."""
    executor = _get_async_executor(name.lower())
    if not executor:
        return f"Unknown async tool: {name}"
    return await executor(args, config)


def execute_tool_call(name: str, args: dict, config: AgentConfig) -> str:
    """Dispatch a tool call to the appropriate executor."""
    executor = EXECUTORS.get(name.lower())
    if not executor:
        return f"Unknown tool: {name}"
    return executor(args, config)
