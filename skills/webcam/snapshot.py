#!/usr/bin/env python3
"""webcam CLI — capture one frame from a camera and describe it with the vision model.

Two steps in one command so the agent never has to "look" at a file itself
(there is no tool that puts an on-disk image in front of the model):

1. **Capture** — ffmpeg grabs a short burst of frames from ``WEBCAM_DEVICE``
   (default ``/dev/video0``) and keeps the last one, so auto-exposure has
   settled. A device path is read via v4l2; anything else (``http://``,
   ``rtsp://``) is passed to ffmpeg as a stream URL, so IP cameras work too.
   The JPEG lands in ``context/workspace/generated/`` where the local console
   Files rail lists it and ``attach`` can deliver it.
2. **Describe** — ``src.llm.describe_image`` sends the frame to ``VISION_MODEL``
   (falling back to ``MODEL`` only when litellm says it accepts images) with
   the user's question as the prompt, and the text comes back on stdout.

Prints JSON on stdout: ``{"path", "device", "model", "description"}`` on
success; ``{"error", "hint"}`` and exit 1 on failure. ``--no-describe`` skips
step 2 (capture only).

The agent invokes this via the bash tool, from the repo root:

    python skills/webcam/snapshot.py --prompt "Is anyone at the desk?"

Tests import it by path and inject a fake runner / describer, so no test
touches a camera or the network.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DEFAULT_DEVICE = "/dev/video0"
DEFAULT_OUT_DIR = ROOT / "context" / "workspace" / "generated"
DEFAULT_WARMUP = 10  # frames captured before the one we keep
CAPTURE_TIMEOUT = 30  # seconds before we give up on ffmpeg


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="snapshot.py",
        description="Capture one webcam frame and describe it with the vision model.",
    )
    p.add_argument(
        "--prompt", default="",
        help="The user's question about the scene; steers the description.",
    )
    p.add_argument(
        "--device", default=os.environ.get("WEBCAM_DEVICE", DEFAULT_DEVICE),
        help="v4l2 device path or stream URL (default: $WEBCAM_DEVICE or /dev/video0).",
    )
    p.add_argument(
        "--out-dir", default=str(DEFAULT_OUT_DIR), dest="out_dir",
        help="Directory for the JPEG (default: context/workspace/generated).",
    )
    p.add_argument(
        "--resolution", default=os.environ.get("WEBCAM_RESOLUTION", ""),
        help="WxH passed to the camera, e.g. 1280x720 (default: camera's choice).",
    )
    p.add_argument(
        "--warmup", type=int, default=DEFAULT_WARMUP,
        help=f"Frames to discard while exposure settles (default {DEFAULT_WARMUP}).",
    )
    p.add_argument(
        "--no-describe", action="store_true", dest="no_describe",
        help="Capture only; skip the vision-model description.",
    )
    return p


def build_ffmpeg_cmd(device: str, out_path: Path, *, warmup: int, resolution: str) -> list[str]:
    """ffmpeg argv: grab ``warmup + 1`` frames, overwriting ``out_path`` each time.

    ``-update 1`` rewrites the same file per frame, so the last (settled)
    frame is what remains. Device paths go through v4l2; URLs are passed as-is.
    """
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    if device.startswith("/dev/"):
        cmd += ["-f", "v4l2"]
        if resolution:
            cmd += ["-video_size", resolution]
    cmd += ["-i", device, "-frames:v", str(max(warmup, 0) + 1), "-update", "1", "-q:v", "2", str(out_path)]
    return cmd


def _default_runner(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=CAPTURE_TIMEOUT)


def capture(device: str, out_dir: Path, *, warmup: int = DEFAULT_WARMUP, resolution: str = "",
            runner=_default_runner, now: datetime | None = None) -> Path:
    """Write one JPEG under ``out_dir`` and return its path. Raises RuntimeError on failure."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH (rebuild the image; the Dockerfile installs it)")
    if device.startswith("/dev/") and not Path(device).exists():
        raise RuntimeError(
            f"camera device {device} not found — is the container started with the "
            "docker-compose.webcam.yml override (devices: + group_add:)?"
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    out_path = out_dir / f"webcam-{stamp}.jpg"
    cmd = build_ffmpeg_cmd(device, out_path, warmup=warmup, resolution=resolution)
    try:
        proc = runner(cmd)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"ffmpeg timed out after {CAPTURE_TIMEOUT}s reading {device}")
    if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        detail = (proc.stderr or "").strip() or f"exit {proc.returncode}"
        raise RuntimeError(f"ffmpeg failed to capture from {device}: {detail}")
    return out_path


def resolve_vision_model(env: dict | None = None, supports_vision=None) -> str:
    """``VISION_MODEL`` first; ``MODEL`` only if litellm confirms it accepts images."""
    env = os.environ if env is None else env
    vision = (env.get("VISION_MODEL") or "").strip()
    if vision:
        return vision
    main = (env.get("MODEL") or "").strip()
    if main:
        if supports_vision is None:
            import litellm  # noqa: PLC0415 — deferred so tests stay fast
            supports_vision = litellm.supports_vision
        try:
            if supports_vision(model=main):
                return main
        except Exception:
            pass
    raise RuntimeError("no vision-capable model configured: set VISION_MODEL in .env")


async def _describe(model: str, path: Path, prompt: str) -> str:
    from src.llm import describe_image  # noqa: PLC0415
    return await describe_image(model, str(path), "image/jpeg", prompt,
                                api_base=os.environ.get("API_BASE") or None)


def cmd_snapshot(args: argparse.Namespace, *, runner=_default_runner, describer=None) -> dict:
    """Capture (and optionally describe) one frame. Returns the result dict or raises RuntimeError."""
    path = capture(args.device, Path(args.out_dir), warmup=args.warmup,
                   resolution=args.resolution, runner=runner)
    result = {"path": str(path), "device": args.device}
    if args.no_describe:
        return result
    model = resolve_vision_model()
    describe = describer or (lambda m, p, q: asyncio.run(_describe(m, p, q)))
    try:
        description = describe(model, path, args.prompt)
    except Exception as exc:  # provider errors surface as JSON, not a traceback
        raise RuntimeError(f"vision model {model} failed: {exc}") from exc
    result.update(model=model, description=description)
    return result


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = cmd_snapshot(args)
    except RuntimeError as exc:
        print(json.dumps({
            "error": str(exc),
            "hint": "Check the camera device, the compose override, and VISION_MODEL; "
                    "report the error to the user rather than retrying blindly.",
        }))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
