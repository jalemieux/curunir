import asyncio

from src.config import AgentConfig
from src.tools.attach import exec_attach
from src.tools.bash_tool import exec_bash
from src.tools.fs_tools import exec_edit, exec_glob, exec_grep, exec_read, exec_write
from src.tools.skill_tool import exec_load_skill
from src.tools.schedule_tool import exec_schedule
from src.tools.web_fetch import exec_web_fetch

# Sync executors — wrapped in asyncio.to_thread at call time
_SYNC_EXECUTORS = {
    "glob": exec_glob,
    "grep": exec_grep,
    "read": exec_read,
    "edit": exec_edit,
    "write": exec_write,
    "bash": exec_bash,
    "load_skill": exec_load_skill,
    "web_fetch": exec_web_fetch,
    "attach": exec_attach,
    "schedule": exec_schedule,
}


def _get_native_async_executor(name: str):
    """Lazily import native async executors to avoid circular dependencies."""
    if name == "run_skill":
        from src.tools.run_skill import exec_run_skill
        return exec_run_skill
    return None


async def execute_tool_call(
    name: str, args: dict, config: AgentConfig,
    attachments: list[dict] | None = None,
    on_tool_call=None,
) -> str:
    """Dispatch a tool call. Sync tools run in a thread, async tools are awaited directly."""
    key = name.lower()

    # Check native async executors first (e.g. run_skill)
    async_executor = _get_native_async_executor(key)
    if async_executor:
        return await async_executor(args, config, on_tool_call=on_tool_call)

    # Sync executors run in a thread to avoid blocking the event loop
    sync_executor = _SYNC_EXECUTORS.get(key)
    if sync_executor:
        # Pass attachments list to tools that accept it (e.g. attach)
        if key == "attach":
            result = await asyncio.to_thread(sync_executor, args, config, attachments)
        else:
            result = await asyncio.to_thread(sync_executor, args, config)

        return result

    return f"Unknown tool: {name}"
