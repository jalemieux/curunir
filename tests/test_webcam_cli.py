"""Tests for the webcam skill CLI (skills/webcam/snapshot.py).

Loaded by path (it lives under skills/, not an importable package). Capture is
driven with a fake ffmpeg runner and description with a fake describer, so no
test touches a camera, ffmpeg, or the network.
"""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_cli():
    spec = importlib.util.spec_from_file_location(
        "webcam_cli", ROOT / "skills" / "webcam" / "snapshot.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cli = _load_cli()


def _parse(*argv):
    return cli.build_parser().parse_args(list(argv))


def _ok_runner(cmd):
    """Fake ffmpeg: writes a non-empty file at the output path and exits 0."""
    Path(cmd[-1]).write_bytes(b"\xff\xd8fakejpeg")
    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


@pytest.fixture(autouse=True)
def _ffmpeg_on_path(monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/ffmpeg")


# --- parser -----------------------------------------------------------------

def test_parser_defaults(monkeypatch):
    monkeypatch.delenv("WEBCAM_DEVICE", raising=False)
    args = _parse()
    assert args.device == "/dev/video0"
    assert args.prompt == ""
    assert args.warmup == cli.DEFAULT_WARMUP
    assert args.no_describe is False


def test_parser_reads_device_from_env(monkeypatch):
    monkeypatch.setenv("WEBCAM_DEVICE", "/dev/video2")
    assert _load_cli().build_parser().parse_args([]).device == "/dev/video2"


# --- ffmpeg argv --------------------------------------------------------------

def test_ffmpeg_cmd_v4l2_device_keeps_last_frame():
    cmd = cli.build_ffmpeg_cmd("/dev/video0", Path("/x/out.jpg"), warmup=10, resolution="1280x720")
    assert cmd[0] == "ffmpeg"
    assert cmd[cmd.index("-f") + 1] == "v4l2"
    assert cmd[cmd.index("-video_size") + 1] == "1280x720"
    assert cmd[cmd.index("-frames:v") + 1] == "11"
    assert cmd[cmd.index("-update") + 1] == "1"
    assert cmd[-1] == "/x/out.jpg"


def test_ffmpeg_cmd_stream_url_skips_v4l2():
    cmd = cli.build_ffmpeg_cmd("rtsp://cam.local/live", Path("/x/out.jpg"), warmup=0, resolution="640x480")
    assert "-f" not in cmd
    assert "-video_size" not in cmd
    assert cmd[cmd.index("-i") + 1] == "rtsp://cam.local/live"
    assert cmd[cmd.index("-frames:v") + 1] == "1"


# --- capture ------------------------------------------------------------------

def test_capture_writes_timestamped_jpeg(tmp_path):
    out = cli.capture("http://cam/snap", tmp_path / "gen", runner=_ok_runner)
    assert out.parent == tmp_path / "gen"
    assert out.name.startswith("webcam-") and out.suffix == ".jpg"
    assert out.stat().st_size > 0


def test_capture_missing_device_is_clear_error(tmp_path):
    with pytest.raises(RuntimeError, match="not found"):
        cli.capture("/dev/video-does-not-exist", tmp_path, runner=_ok_runner)


def test_capture_missing_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="ffmpeg not found"):
        cli.capture("http://cam/snap", tmp_path, runner=_ok_runner)


def test_capture_ffmpeg_failure_surfaces_stderr(tmp_path):
    def bad(cmd):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Permission denied")
    with pytest.raises(RuntimeError, match="Permission denied"):
        cli.capture("http://cam/snap", tmp_path, runner=bad)


def test_capture_empty_output_is_failure(tmp_path):
    def empty(cmd):
        Path(cmd[-1]).write_bytes(b"")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    with pytest.raises(RuntimeError, match="failed to capture"):
        cli.capture("http://cam/snap", tmp_path, runner=empty)


def test_capture_timeout(tmp_path):
    def slow(cmd):
        raise subprocess.TimeoutExpired(cmd, cli.CAPTURE_TIMEOUT)
    with pytest.raises(RuntimeError, match="timed out"):
        cli.capture("http://cam/snap", tmp_path, runner=slow)


# --- model resolution ---------------------------------------------------------

def test_resolve_prefers_vision_model():
    env = {"VISION_MODEL": "gemini/gemini-2.5-flash", "MODEL": "openai/gpt-4o"}
    assert cli.resolve_vision_model(env, supports_vision=lambda model: True) == "gemini/gemini-2.5-flash"


def test_resolve_falls_back_to_vision_capable_main_model():
    env = {"MODEL": "openai/gpt-4o"}
    assert cli.resolve_vision_model(env, supports_vision=lambda model: True) == "openai/gpt-4o"


def test_resolve_rejects_text_only_main_model():
    env = {"MODEL": "ollama/llama3"}
    with pytest.raises(RuntimeError, match="VISION_MODEL"):
        cli.resolve_vision_model(env, supports_vision=lambda model: False)


def test_resolve_nothing_configured():
    with pytest.raises(RuntimeError):
        cli.resolve_vision_model({}, supports_vision=lambda model: False)


# --- end to end ---------------------------------------------------------------

def test_cmd_snapshot_describes_with_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_MODEL", "gemini/gemini-2.5-flash")
    calls = []

    def fake_describe(model, path, prompt):
        calls.append((model, Path(path).name, prompt))
        return "An empty desk."

    args = _parse("--device", "http://cam/snap", "--out-dir", str(tmp_path), "--prompt", "Anyone there?")
    result = cli.cmd_snapshot(args, runner=_ok_runner, describer=fake_describe)
    assert result["description"] == "An empty desk."
    assert result["model"] == "gemini/gemini-2.5-flash"
    assert Path(result["path"]).exists()
    assert calls == [("gemini/gemini-2.5-flash", Path(result["path"]).name, "Anyone there?")]


def test_cmd_snapshot_no_describe_skips_model(tmp_path, monkeypatch):
    monkeypatch.delenv("VISION_MODEL", raising=False)
    monkeypatch.delenv("MODEL", raising=False)
    args = _parse("--device", "http://cam/snap", "--out-dir", str(tmp_path), "--no-describe")
    result = cli.cmd_snapshot(args, runner=_ok_runner, describer=lambda *a: pytest.fail("should not describe"))
    assert "description" not in result and Path(result["path"]).exists()


def test_cmd_snapshot_provider_error_becomes_runtime_error(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_MODEL", "gemini/gemini-2.5-flash")

    def boom(model, path, prompt):
        raise ConnectionError("upstream down")

    args = _parse("--device", "http://cam/snap", "--out-dir", str(tmp_path))
    with pytest.raises(RuntimeError, match="upstream down"):
        cli.cmd_snapshot(args, runner=_ok_runner, describer=boom)


def test_main_prints_json_error_and_exits_1(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    rc = cli.main(["--device", "http://cam/snap", "--out-dir", str(tmp_path)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and "ffmpeg not found" in out["error"] and "hint" in out


# --- api_base resolution ------------------------------------------------------

def test_api_base_explicit_vision_api_base_wins():
    env = {"VISION_API_BASE": "http://cam-host:8083/v1", "API_BASE": "http://main:8080/v1",
           "MODEL": "openai/local", "VISION_MODEL": "openai/qwen3-vl-2b"}
    assert cli.resolve_vision_api_base("openai/qwen3-vl-2b", env) == "http://cam-host:8083/v1"


def test_api_base_not_reused_for_separate_vision_model():
    # MODEL is local (API_BASE set) but VISION_MODEL is a cloud model: sending it
    # to API_BASE would hit the llama.cpp server with a foreign model name.
    env = {"API_BASE": "http://main:8080/v1", "MODEL": "openai/local", "VISION_MODEL": "openai/gpt-4o-mini"}
    assert cli.resolve_vision_api_base("openai/gpt-4o-mini", env) is None


def test_api_base_reused_when_falling_back_to_main_model():
    env = {"API_BASE": "http://main:8080/v1", "MODEL": "openai/qwen3-vl-2b"}
    assert cli.resolve_vision_api_base("openai/qwen3-vl-2b", env) == "http://main:8080/v1"


def test_api_base_none_when_nothing_set():
    assert cli.resolve_vision_api_base("gemini/gemini-2.5-flash", {}) is None
