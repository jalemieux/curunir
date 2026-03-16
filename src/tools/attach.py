import mimetypes
import os

from src.config import AgentConfig


def exec_attach(args: dict, config: AgentConfig, attachments: list[dict] | None = None) -> str:
    """Attach a file to the current response."""
    path = args["path"]
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    name = args.get("name") or os.path.basename(path)
    mime_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    size = os.path.getsize(path)
    attachment = {"filename": name, "path": path, "mime_type": mime_type, "size": size}
    if attachments is not None:
        attachments.append(attachment)
    return f"Attached: {name} ({mime_type}, {size} bytes)"
