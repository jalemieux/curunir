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
