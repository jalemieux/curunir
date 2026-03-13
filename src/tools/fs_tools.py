import glob as glob_module
import re
import subprocess
from pathlib import Path

from src.config import AgentConfig


def exec_glob(args: dict, config: AgentConfig) -> str:
    """Find files matching a glob pattern."""
    try:
        pattern = args["pattern"]
        root = args.get("path", ".")
        matches = glob_module.glob(pattern, root_dir=root, recursive=True)
        return "\n".join(sorted(matches))
    except Exception as e:
        return f"Error: {e}"


def _find_rg() -> str | None:
    """Find the ripgrep binary path."""
    import shutil
    return shutil.which("rg")


def exec_grep(args: dict, config: AgentConfig) -> str:
    """Search file contents using ripgrep if available, else pure Python."""
    pattern = args["pattern"]
    path = args.get("path", ".")
    glob_filter = args.get("glob")
    output_mode = args.get("output_mode", "content")
    context_lines = args.get("context", 0)

    rg_path = _find_rg()
    if rg_path:
        try:
            cmd = [rg_path, "--no-heading"]
            if glob_filter:
                cmd.extend(["--glob", glob_filter])
            if output_mode == "files_with_matches":
                cmd.append("--files-with-matches")
            elif output_mode == "count":
                cmd.append("--count")
            if context_lines:
                cmd.extend(["-C", str(context_lines)])
            cmd.extend([pattern, path])
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return result.stdout if result.stdout else ""
        except Exception as e:
            return f"Error: {e}"

    # Pure Python fallback
    try:
        regex = re.compile(pattern)
        root = Path(path)

        # Collect files to search
        if root.is_file():
            files = [root]
        else:
            if glob_filter:
                files = list(root.glob(glob_filter))
            else:
                files = [f for f in root.rglob("*") if f.is_file()]

        output_lines = []
        for filepath in sorted(files):
            try:
                text = filepath.read_text(errors="replace")
            except Exception:
                continue

            lines = text.splitlines()
            matching_indices = [i for i, line in enumerate(lines) if regex.search(line)]

            if not matching_indices:
                continue

            if output_mode == "files_with_matches":
                output_lines.append(str(filepath))
            elif output_mode == "count":
                output_lines.append(f"{filepath}:{len(matching_indices)}")
            else:
                # content mode with optional context
                shown: set[int] = set()
                for idx in matching_indices:
                    for j in range(max(0, idx - context_lines), min(len(lines), idx + context_lines + 1)):
                        shown.add(j)
                for j in sorted(shown):
                    output_lines.append(f"{filepath}:{j + 1}:{lines[j]}")

        return "\n".join(output_lines)
    except Exception as e:
        return f"Error: {e}"


def exec_read(args: dict, config: AgentConfig) -> str:
    """Read a file with line numbers."""
    try:
        path = Path(args["file_path"])
        if not path.exists():
            return f"Error: File not found: {path}"

        lines = path.read_text().splitlines()
        offset = args.get("offset", 1)
        limit = args.get("limit", len(lines))

        # offset is 1-based
        start = max(0, offset - 1)
        end = start + limit
        selected = lines[start:end]

        numbered = []
        for i, line in enumerate(selected, start=start + 1):
            numbered.append(f"{i}\t{line}")
        return "\n".join(numbered)
    except Exception as e:
        return f"Error: {e}"


def exec_edit(args: dict, config: AgentConfig) -> str:
    """Replace exact string in a file."""
    try:
        path = Path(args["file_path"])
        if not path.exists():
            return f"Error: File not found: {path}"

        content = path.read_text()
        old = args["old_string"]
        new = args["new_string"]
        replace_all = args.get("replace_all", False)

        count = content.count(old)
        if count == 0:
            return f"Error: old_string not found in {path}"
        if count > 1 and not replace_all:
            return f"Error: old_string not unique in {path} (found {count} occurrences). Use replace_all=true to replace all."

        if replace_all:
            new_content = content.replace(old, new)
        else:
            new_content = content.replace(old, new, 1)

        path.write_text(new_content)
        return f"Replaced {count if replace_all else 1} occurrence(s) in {path}"
    except Exception as e:
        return f"Error: {e}"


def exec_write(args: dict, config: AgentConfig) -> str:
    """Write content to a file, creating parent dirs if needed."""
    try:
        path = Path(args["file_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args["content"])
        return f"Wrote {len(args['content'])} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"
