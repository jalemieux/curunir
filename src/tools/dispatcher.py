import asyncio

from src.config import AgentConfig
from src.tools.attach import exec_attach
from src.tools.bash_tool import exec_bash
from src.tools.financial_plan_tool import exec_financial_plan
from src.tools.fs_tools import exec_edit, exec_glob, exec_grep, exec_read, exec_write
from src.tools.portfolio_tool import exec_portfolio
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
    "portfolio": exec_portfolio,
    "financial_plan": exec_financial_plan,
}


def _get_native_async_executor(name: str):
    """Lazily import native async executors to avoid circular dependencies."""
    if name == "delegate":
        from src.tools.delegate import exec_delegate
        return exec_delegate
    if name == "to_audio":
        from src.tools.to_audio import exec_to_audio
        return exec_to_audio
    return None


# Async executors that need the mutable attachments list.
_ASYNC_EXECUTORS_WITH_ATTACHMENTS = {"to_audio"}


async def execute_tool_call(
    name: str, args: dict, config: AgentConfig,
    attachments: list[dict] | None = None,
    on_tool_call=None,
) -> str:
    """Dispatch a tool call. Sync tools run in a thread, async tools are awaited directly."""
    key = name.lower()

    # Check native async executors first (e.g. delegate)
    async_executor = _get_native_async_executor(key)
    if async_executor:
        if key in _ASYNC_EXECUTORS_WITH_ATTACHMENTS:
            return await async_executor(
                args, config, attachments=attachments, on_tool_call=on_tool_call,
            )
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
