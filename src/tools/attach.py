import mimetypes
import os

from src.config import AgentConfig


def attachment_metadata(path: str, name: str | None = None) -> dict:
    """Build the channel-shaped attachment dict for a file path.

    Shared by `exec_attach` (live turn) and history reconstruction (replaying
    a reopened conversation). `size` is 0 when the file is gone — downstream
    enrichment flags missing files, so a deleted artifact degrades gracefully
    rather than raising here.
    """
    return {
        "filename": name or os.path.basename(path),
        "path": path,
        "mime_type": mimetypes.guess_type(path)[0] or "application/octet-stream",
        "size": os.path.getsize(path) if os.path.isfile(path) else 0,
    }


def exec_attach(args: dict, config: AgentConfig, attachments: list[dict] | None = None) -> str:
    """Attach a file to the current response."""
    path = args["path"]
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    name = args.get("name") or os.path.basename(path)
    attachment = attachment_metadata(path, name)
    if attachments is not None:
        attachments.append(attachment)
    return (
        f"Attached: {attachment['filename']} "
        f"({attachment['mime_type']}, {attachment['size']} bytes)"
    )
