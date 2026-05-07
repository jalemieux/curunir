#!/usr/bin/env python3
"""ComfyUI driver CLI.

Exposes the small slice of the ComfyUI API the agent needs to drive a
local instance: enumerate models / nodes, submit a workflow, wait for
completion, fetch outputs, and manage the queue.

All subcommands print JSON to stdout. Errors print
``{"error": "...", "hint": "..."}`` and exit 1; usage errors exit 2.

The agent invokes this via the ``bash`` tool. Tests import it as a
module and call ``cmd_*`` functions directly.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

DEFAULT_URL = "http://127.0.0.1:8188"
DEFAULT_TIMEOUT = 300

# Mapping of model-loader class_type -> (input field name, /object_info category key)
MODEL_LOADERS = {
    "CheckpointLoaderSimple": ("ckpt_name", "checkpoints"),
    "UNETLoader": ("unet_name", "checkpoints"),
    "VAELoader": ("vae_name", "vaes"),
    "LoraLoader": ("lora_name", "loras"),
    "LoraLoaderModelOnly": ("lora_name", "loras"),
    "ControlNetLoader": ("control_net_name", "controlnets"),
}

# Mapping for `models` summary output: category key -> list of (class, field) sources.
MODEL_CATEGORIES = {
    "checkpoints": [("CheckpointLoaderSimple", "ckpt_name"), ("UNETLoader", "unet_name")],
    "loras": [("LoraLoader", "lora_name"), ("LoraLoaderModelOnly", "lora_name")],
    "vaes": [("VAELoader", "vae_name")],
    "controlnets": [("ControlNetLoader", "control_net_name")],
}


class DriverError(Exception):
    """Driver-level error (network, missing data, etc)."""

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


class PreflightError(DriverError):
    """Workflow failed pre-flight validation against the live server."""


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _client(base_url: str, timeout: float = 30.0) -> httpx.Client:
    return httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)


def _get_object_info(base_url: str) -> dict[str, Any]:
    try:
        with _client(base_url) as c:
            r = c.get("/object_info")
            r.raise_for_status()
            return r.json()
    except httpx.ConnectError as e:
        raise DriverError(
            f"could not reach ComfyUI at {base_url}: {e}",
            hint=f"local ComfyUI process isn't running on {base_url} — start it and retry",
        ) from e


def _extract_enum(class_info: dict, field: str) -> list[str]:
    """ComfyUI encodes enums as ``[[v1, v2, ...], {...metadata}]`` or ``[[...]]``."""
    required = class_info.get("input", {}).get("required", {})
    spec = required.get(field)
    if not spec:
        return []
    first = spec[0] if isinstance(spec, list) else spec
    if isinstance(first, list):
        return [v for v in first if isinstance(v, str)]
    return []


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_models(base_url: str) -> dict[str, list[str]]:
    info = _get_object_info(base_url)
    out: dict[str, list[str]] = {}
    for category, sources in MODEL_CATEGORIES.items():
        merged: list[str] = []
        for cls, field in sources:
            if cls in info:
                merged.extend(_extract_enum(info[cls], field))
        # de-dupe while preserving order
        seen: set[str] = set()
        out[category] = [m for m in merged if not (m in seen or seen.add(m))]
    return out


def cmd_nodes(base_url: str, required: list[str] | None = None) -> dict[str, list[str]]:
    info = _get_object_info(base_url)
    available = sorted(info.keys())
    result: dict[str, list[str]] = {"available": available}
    if required is not None:
        result["missing"] = [c for c in required if c not in info]
    return result


def _preflight(workflow: dict, info: dict) -> None:
    """Raise PreflightError if any node class or named model is missing."""
    missing_nodes: list[str] = []
    missing_models: list[str] = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        cls = node.get("class_type")
        if not cls:
            continue
        if cls not in info:
            missing_nodes.append(cls)
            continue
        spec = MODEL_LOADERS.get(cls)
        if spec is None:
            continue
        field, category_key = spec
        name = node.get("inputs", {}).get(field)
        if not isinstance(name, str):
            continue
        valid = _extract_enum(info[cls], field)
        if valid and name not in valid:
            missing_models.append(f"{name} (used by {cls} at node {node_id})")
    if missing_nodes:
        unique = sorted(set(missing_nodes))
        raise PreflightError(
            f"workflow references unknown node classes: {', '.join(unique)}",
            hint="install the required custom nodes via ComfyUI Manager and retry",
        )
    if missing_models:
        first = missing_models[0].split(" ", 1)[0]
        raise PreflightError(
            f"required model not found locally: {'; '.join(missing_models)}",
            hint=f"install `{first}` via ComfyUI Manager (or check the filename) and retry",
        )


def _load_workflow(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise DriverError(f"could not read workflow {path}: {e}") from e
    # Strip the optional `_meta` block — ComfyUI rejects unknown top-level keys.
    return {k: v for k, v in data.items() if k != "_meta"}


def cmd_submit(base_url: str, workflow_path: Path, client_id: str | None = None) -> dict:
    workflow = _load_workflow(workflow_path)
    info = _get_object_info(base_url)
    _preflight(workflow, info)
    client_id = client_id or f"curunir-{uuid.uuid4().hex[:12]}"
    body = {"prompt": workflow, "client_id": client_id}
    with _client(base_url) as c:
        r = c.post("/prompt", json=body)
        if r.status_code >= 400:
            raise DriverError(
                f"ComfyUI rejected the prompt: HTTP {r.status_code} {r.text[:300]}",
                hint="re-check the workflow JSON; ComfyUI's error usually points at the offending node",
            )
        data = r.json()
    return {
        "prompt_id": data.get("prompt_id"),
        "queue_position": data.get("number"),
        "client_id": client_id,
    }


def _history(base_url: str, prompt_id: str) -> dict | None:
    with _client(base_url) as c:
        r = c.get(f"/history/{prompt_id}")
        r.raise_for_status()
        data = r.json()
    return data.get(prompt_id)


def _is_complete(entry: dict) -> bool:
    status = entry.get("status", {})
    if status.get("completed"):
        return True
    if status.get("status_str") in {"error", "success"}:
        return True
    return False


def _summarize(entry: dict, prompt_id: str) -> dict:
    status = entry.get("status", {})
    if status.get("status_str") == "error" or any(
        m and m[0] == "execution_error" for m in status.get("messages", [])
    ):
        msg = ""
        for m in status.get("messages", []):
            if m and m[0] == "execution_error":
                msg = m[1].get("exception_message", "") if isinstance(m[1], dict) else str(m[1])
                break
        return {
            "status": "error",
            "prompt_id": prompt_id,
            "error": msg or "ComfyUI reported an execution error",
            "outputs": entry.get("outputs", {}),
        }
    return {
        "status": "done",
        "prompt_id": prompt_id,
        "outputs": entry.get("outputs", {}),
    }


def _wait_via_polling(base_url: str, prompt_id: str, timeout: float, poll_interval: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        entry = _history(base_url, prompt_id)
        if entry and _is_complete(entry):
            return _summarize(entry, prompt_id)
        time.sleep(poll_interval)
    return {"status": "timeout", "prompt_id": prompt_id}


def _wait_via_ws(base_url: str, prompt_id: str, client_id: str, timeout: float) -> dict:
    """Watch the WebSocket for an end-of-execution frame for prompt_id.

    Returns a summary dict or raises DriverError if the WS connection fails.
    """
    from websockets.sync.client import connect

    ws_url = base_url.replace("http://", "ws://").replace("https://", "wss://").rstrip("/")
    ws_url = f"{ws_url}/ws?clientId={client_id}"
    deadline = time.monotonic() + timeout
    try:
        with connect(ws_url, open_timeout=5, close_timeout=2) as ws:
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                try:
                    raw = ws.recv(timeout=min(remaining, 30))
                except TimeoutError:
                    continue
                if isinstance(raw, bytes):
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("type") != "executing":
                    continue
                data = msg.get("data", {})
                if data.get("prompt_id") != prompt_id:
                    continue
                if data.get("node") is None:
                    break
    except Exception as e:  # noqa: BLE001 - any WS failure falls back to polling
        raise DriverError(f"websocket failed: {e}") from e
    entry = _history(base_url, prompt_id)
    if entry and _is_complete(entry):
        return _summarize(entry, prompt_id)
    return {"status": "timeout", "prompt_id": prompt_id}


def cmd_wait(
    base_url: str,
    prompt_id: str,
    timeout: float = DEFAULT_TIMEOUT,
    client_id: str | None = None,
    poll_only: bool = False,
    poll_interval: float = 1.0,
) -> dict:
    if not poll_only and client_id:
        try:
            return _wait_via_ws(base_url, prompt_id, client_id, timeout)
        except DriverError:
            pass  # fall through to polling
    return _wait_via_polling(base_url, prompt_id, timeout, poll_interval)


def _download_view(base_url: str, filename: str, subfolder: str, type_: str, dest: Path) -> None:
    params = {"filename": filename, "subfolder": subfolder, "type": type_}
    with _client(base_url, timeout=120.0) as c:
        r = c.get("/view", params=params)
        if r.status_code >= 400:
            raise DriverError(f"could not download {filename}: HTTP {r.status_code}")
        dest.write_bytes(r.content)


def cmd_fetch(base_url: str, prompt_id: str, out_dir: Path) -> dict:
    entry = _history(base_url, prompt_id)
    if entry is None:
        raise DriverError(
            f"no history for prompt_id={prompt_id}",
            hint="confirm the prompt_id is correct and that the prompt has finished",
        )
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files: list[dict] = []
    for node_id, node_outputs in (entry.get("outputs") or {}).items():
        if not isinstance(node_outputs, dict):
            continue
        for kind in ("images", "gifs", "videos", "audio"):
            for item in node_outputs.get(kind, []) or []:
                filename = item.get("filename")
                if not filename:
                    continue
                subfolder = item.get("subfolder", "")
                type_ = item.get("type", "output")
                local_name = f"{node_id}_{filename}" if any(f["name"] == filename for f in files) else filename
                dest = out_dir / local_name
                _download_view(base_url, filename, subfolder, type_, dest)
                files.append(
                    {
                        "path": str(dest),
                        "name": local_name,
                        "node": node_id,
                        "kind": kind,
                    }
                )
    return {"prompt_id": prompt_id, "files": files}


def cmd_run(
    base_url: str,
    workflow_path: Path,
    out_dir: Path,
    timeout: float = DEFAULT_TIMEOUT,
    poll_only: bool = False,
    poll_interval: float = 1.0,
) -> dict:
    sub = cmd_submit(base_url, workflow_path)
    prompt_id = sub["prompt_id"]
    client_id = sub.get("client_id")
    waited = cmd_wait(
        base_url,
        prompt_id,
        timeout=timeout,
        client_id=client_id,
        poll_only=poll_only,
        poll_interval=poll_interval,
    )
    if waited["status"] != "done":
        return {**waited, "files": []}
    fetched = cmd_fetch(base_url, prompt_id, out_dir)
    return {
        "status": "done",
        "prompt_id": prompt_id,
        "files": fetched["files"],
        "outputs": waited.get("outputs", {}),
    }


def cmd_queue(base_url: str) -> dict:
    with _client(base_url) as c:
        r = c.get("/queue")
        r.raise_for_status()
        data = r.json()
    return {
        "running": data.get("queue_running", []),
        "pending": data.get("queue_pending", []),
    }


def cmd_cancel(base_url: str, prompt_id: str) -> dict:
    q = cmd_queue(base_url)
    is_running = any(
        len(item) > 1 and item[1] == prompt_id for item in q["running"]
    )
    with _client(base_url) as c:
        if is_running:
            r = c.post("/interrupt", json={})
            r.raise_for_status()
            return {"cancelled": prompt_id, "method": "interrupt"}
        r = c.post("/queue", json={"delete": [prompt_id]})
        r.raise_for_status()
    return {"cancelled": prompt_id, "method": "delete"}


def cmd_history(base_url: str, limit: int = 20) -> dict:
    with _client(base_url) as c:
        r = c.get("/history", params={"max_items": limit})
        r.raise_for_status()
        data = r.json()
    items = []
    for prompt_id, entry in list(data.items())[-limit:]:
        status = entry.get("status", {})
        items.append(
            {
                "prompt_id": prompt_id,
                "completed": status.get("completed", False),
                "status": status.get("status_str", "unknown"),
            }
        )
    return {"items": items}


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="comfy.py", description="ComfyUI driver CLI")
    p.add_argument("--url", default=os.environ.get("COMFYUI_URL", DEFAULT_URL))
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("models", help="list available checkpoints/loras/vaes/controlnets")

    nodes = sub.add_parser("nodes", help="list available node classes")
    nodes.add_argument("--required", help="comma-separated class names to check for", default=None)

    submit = sub.add_parser("submit", help="submit a workflow JSON")
    submit.add_argument("workflow", help="path to workflow JSON")

    wait = sub.add_parser("wait", help="wait for a prompt to complete")
    wait.add_argument("prompt_id")
    wait.add_argument("--timeout", type=float, default=float(os.environ.get("COMFYUI_DEFAULT_TIMEOUT_S", DEFAULT_TIMEOUT)))
    wait.add_argument("--client-id", default=None)
    wait.add_argument("--poll-only", action="store_true", help="skip WS, poll /history")

    fetch = sub.add_parser("fetch", help="download outputs of a finished prompt")
    fetch.add_argument("prompt_id")
    fetch.add_argument("--out", required=True)

    run = sub.add_parser("run", help="submit + wait + fetch")
    run.add_argument("workflow", help="path to workflow JSON")
    run.add_argument("--out", required=True)
    run.add_argument("--timeout", type=float, default=float(os.environ.get("COMFYUI_DEFAULT_TIMEOUT_S", DEFAULT_TIMEOUT)))
    run.add_argument("--poll-only", action="store_true")

    sub.add_parser("queue", help="show running and pending prompts")

    cancel = sub.add_parser("cancel", help="cancel a queued or running prompt")
    cancel.add_argument("prompt_id")

    history = sub.add_parser("history", help="recent prompt IDs and status")
    history.add_argument("--limit", type=int, default=20)

    return p


def _emit(payload: Any) -> None:
    json.dump(payload, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    url = args.url
    try:
        if args.cmd == "models":
            _emit(cmd_models(url))
        elif args.cmd == "nodes":
            required = [c.strip() for c in args.required.split(",")] if args.required else None
            _emit(cmd_nodes(url, required=required))
        elif args.cmd == "submit":
            _emit(cmd_submit(url, Path(args.workflow)))
        elif args.cmd == "wait":
            _emit(cmd_wait(url, args.prompt_id, timeout=args.timeout, client_id=args.client_id, poll_only=args.poll_only))
        elif args.cmd == "fetch":
            _emit(cmd_fetch(url, args.prompt_id, Path(args.out)))
        elif args.cmd == "run":
            _emit(cmd_run(url, Path(args.workflow), Path(args.out), timeout=args.timeout, poll_only=args.poll_only))
        elif args.cmd == "queue":
            _emit(cmd_queue(url))
        elif args.cmd == "cancel":
            _emit(cmd_cancel(url, args.prompt_id))
        elif args.cmd == "history":
            _emit(cmd_history(url, limit=args.limit))
        else:
            return 2
    except DriverError as e:
        _emit({"error": str(e), "hint": getattr(e, "hint", "")})
        return 1
    except httpx.HTTPError as e:
        _emit({"error": f"HTTP error: {e}", "hint": "is ComfyUI running and reachable on the configured URL?"})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
