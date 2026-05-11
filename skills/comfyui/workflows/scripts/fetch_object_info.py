#!/usr/bin/env python3
"""Fetch /object_info from a local ComfyUI instance and write a normalized
schema to disk for the agent to read.

Usage:
    python fetch_object_info.py [--out PATH] [--classes CLS [CLS ...]] [--url URL]

Defaults:
    URL: http://127.0.0.1:8188 (override with COMFYUI_URL or --url)
    OUT: ./object_info.normalized.json

The normalized output collapses required+optional inputs into a single
{name: {type, required, default?, choices?}} mapping per node class,
which is what the agent actually needs when authoring /prompt JSON.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

import httpx

DEFAULT_URL = "http://127.0.0.1:8188"
DEFAULT_OUT = "object_info.normalized.json"
TIMEOUT_SECONDS = 30.0


def resolve_url(cli_url: str | None = None) -> str:
    if cli_url:
        return cli_url
    return os.environ.get("COMFYUI_URL", DEFAULT_URL)


def fetch_object_info(url: str) -> dict[str, Any]:
    endpoint = url.rstrip("/") + "/object_info"
    resp = httpx.get(endpoint, timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp.json()


def _normalize_input(spec: Any) -> dict[str, Any]:
    """Convert one raw input spec to {type, default?, choices?, min?, max?, step?}.

    Raw forms seen in /object_info:
        ["INT"]
        ["INT", {"default": 20, "min": 1, "max": 1000}]
        [["a", "b"], {"default": "a"}]              # enum
        [["a", "b"]]                                # enum, no metadata
    """
    if not isinstance(spec, list) or not spec:
        return {"type": "UNKNOWN"}

    type_field = spec[0]
    meta: dict[str, Any] = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}

    if isinstance(type_field, list):
        out: dict[str, Any] = {"type": "ENUM", "choices": list(type_field)}
    else:
        out = {"type": str(type_field)}

    for key in ("default", "min", "max", "step", "multiline"):
        if key in meta:
            out[key] = meta[key]
    return out


def normalize(
    raw: dict[str, Any],
    classes: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Reduce raw /object_info to a compact authoring-time schema.

    If ``classes`` is given, return only those node classes. Unknown class
    names are silently skipped — call sites can detect an empty result.
    """
    selected: set[str] | None = set(classes) if classes is not None else None
    out: dict[str, Any] = {}

    for class_name, body in raw.items():
        if selected is not None and class_name not in selected:
            continue
        if not isinstance(body, dict):
            continue

        inputs_section = body.get("input", {}) or {}
        required = inputs_section.get("required", {}) or {}
        optional = inputs_section.get("optional", {}) or {}

        normalized_inputs: dict[str, Any] = {}
        for input_name, spec in required.items():
            entry = _normalize_input(spec)
            entry["required"] = True
            normalized_inputs[input_name] = entry
        for input_name, spec in optional.items():
            entry = _normalize_input(spec)
            entry["required"] = False
            normalized_inputs[input_name] = entry

        out[class_name] = {
            "display_name": body.get("display_name", class_name),
            "category": body.get("category", ""),
            "description": body.get("description", ""),
            "inputs": normalized_inputs,
            "outputs": list(body.get("output", []) or []),
            "output_names": list(body.get("output_name", []) or []),
        }

    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch and normalize ComfyUI /object_info.")
    parser.add_argument("--url", help="ComfyUI base URL (default: $COMFYUI_URL or http://127.0.0.1:8188)")
    parser.add_argument("--out", default=DEFAULT_OUT, help="Output path for normalized JSON")
    parser.add_argument(
        "--classes",
        nargs="+",
        help="Only emit these node class names (default: all)",
    )
    args = parser.parse_args(argv)

    url = resolve_url(args.url)
    try:
        raw = fetch_object_info(url)
    except httpx.HTTPError as exc:
        print(f"failed to fetch {url}/object_info: {exc}", file=sys.stderr)
        return 1

    normalized = normalize(raw, classes=args.classes)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(normalized, indent=2, sort_keys=True))
    print(f"wrote {len(normalized)} node schemas to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
