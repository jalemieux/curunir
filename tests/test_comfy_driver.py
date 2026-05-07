"""Unit tests for skills/comfyui/comfy.py against a fake ComfyUI server.

The fake server speaks the subset of the ComfyUI HTTP API the driver uses
(`/object_info`, `/prompt`, `/history/{id}`, `/view`, `/queue`,
`/interrupt`) and an optional WebSocket endpoint for `wait`.

Tests import the driver as a module and call subcommand functions
directly. The CLI surface (argparse + JSON stdout) is exercised once via
subprocess to confirm wiring.
"""

from __future__ import annotations

import importlib.util
import json
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
COMFY_PATH = REPO_ROOT / "skills" / "comfyui" / "comfy.py"


@pytest.fixture(scope="module")
def comfy():
    """Load comfy.py as a module without requiring a package layout."""
    spec = importlib.util.spec_from_file_location("comfy", COMFY_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _object_info_fixture(checkpoints=("flux1-dev.safetensors",), node_classes=None):
    """Build an /object_info payload that matches ComfyUI's shape."""
    classes = list(node_classes or ["KSampler", "CLIPTextEncode", "LoadImage", "SaveImage", "CheckpointLoaderSimple"])
    info = {}
    for cls in classes:
        info[cls] = {"input": {"required": {}}}
    if "CheckpointLoaderSimple" in info:
        info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"] = [list(checkpoints)]
    return info


class FakeComfyHandler(BaseHTTPRequestHandler):
    """Routes set on the server instance: server.routes is a dict-of-dicts."""

    def log_message(self, *args, **kwargs):  # silence test output
        pass

    def _send_json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, code, content_type, body):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        routes = self.server.routes

        if path == "/object_info":
            self._send_json(200, routes["object_info"])
            return
        if path.startswith("/history/"):
            prompt_id = path.split("/", 2)[2]
            history = routes.get("history", {}).get(prompt_id)
            if history is None:
                self._send_json(200, {})
            else:
                self._send_json(200, {prompt_id: history})
            return
        if path == "/view":
            qs = parse_qs(url.query)
            filename = qs.get("filename", [""])[0]
            files = routes.get("view_files", {})
            blob = files.get(filename)
            if blob is None:
                self._send_bytes(404, "text/plain", b"not found")
                return
            self._send_bytes(200, "image/png", blob)
            return
        if path == "/queue":
            self._send_json(200, routes.get("queue", {"queue_running": [], "queue_pending": []}))
            return
        self._send_bytes(404, "text/plain", b"not found")

    def do_POST(self):
        url = urlparse(self.path)
        path = url.path
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            payload = {}
        routes = self.server.routes

        if path == "/prompt":
            routes.setdefault("submitted", []).append(payload)
            response = routes.get("prompt_response", {"prompt_id": "test-id-1", "number": 1})
            self._send_json(200, response)
            return
        if path == "/queue":
            routes.setdefault("queue_actions", []).append(payload)
            self._send_json(200, {})
            return
        if path == "/interrupt":
            routes.setdefault("interrupt_calls", []).append(True)
            self._send_json(200, {})
            return
        self._send_bytes(404, "text/plain", b"not found")


class FakeServer:
    def __init__(self):
        self.port = _free_port()
        self.routes: dict = {
            "object_info": _object_info_fixture(),
            "history": {},
            "view_files": {},
            "queue": {"queue_running": [], "queue_pending": []},
        }
        self.httpd = HTTPServer(("127.0.0.1", self.port), FakeComfyHandler)
        self.httpd.routes = self.routes
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)


@pytest.fixture
def server():
    s = FakeServer().start()
    try:
        yield s
    finally:
        s.stop()


@pytest.fixture
def workflow_path(tmp_path):
    wf = {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "flux1-dev.safetensors"},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "a cat"},
        },
        "3": {
            "class_type": "KSampler",
            "inputs": {"seed": 42},
        },
        "4": {
            "class_type": "SaveImage",
            "inputs": {},
        },
    }
    p = tmp_path / "workflow.json"
    p.write_text(json.dumps(wf))
    return p


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


class TestModels:
    def test_returns_checkpoints(self, comfy, server):
        result = comfy.cmd_models(server.url)
        assert "flux1-dev.safetensors" in result["checkpoints"]

    def test_groups_known_categories(self, comfy, server):
        server.routes["object_info"] = {
            "CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["a.safetensors"]]}}},
            "LoraLoader": {"input": {"required": {"lora_name": [["lora.safetensors"]]}}},
            "VAELoader": {"input": {"required": {"vae_name": [["vae.safetensors"]]}}},
            "ControlNetLoader": {"input": {"required": {"control_net_name": [["cn.safetensors"]]}}},
        }
        result = comfy.cmd_models(server.url)
        assert result["checkpoints"] == ["a.safetensors"]
        assert result["loras"] == ["lora.safetensors"]
        assert result["vaes"] == ["vae.safetensors"]
        assert result["controlnets"] == ["cn.safetensors"]


# ---------------------------------------------------------------------------
# nodes
# ---------------------------------------------------------------------------


class TestNodes:
    def test_lists_available(self, comfy, server):
        result = comfy.cmd_nodes(server.url)
        assert "KSampler" in result["available"]
        assert "CLIPTextEncode" in result["available"]

    def test_required_check_reports_missing(self, comfy, server):
        result = comfy.cmd_nodes(server.url, required=["KSampler", "NotAClass", "AlsoMissing"])
        assert set(result["missing"]) == {"NotAClass", "AlsoMissing"}


# ---------------------------------------------------------------------------
# submit
# ---------------------------------------------------------------------------


class TestSubmit:
    def test_happy_path(self, comfy, server, workflow_path):
        result = comfy.cmd_submit(server.url, workflow_path)
        assert result["prompt_id"] == "test-id-1"
        submitted = server.routes["submitted"][0]
        assert "prompt" in submitted
        assert submitted["prompt"]["1"]["class_type"] == "CheckpointLoaderSimple"
        assert "client_id" in submitted

    def test_preflight_rejects_unknown_node(self, comfy, server, tmp_path):
        wf = {"1": {"class_type": "MadeUpNode", "inputs": {}}}
        p = tmp_path / "bad.json"
        p.write_text(json.dumps(wf))
        with pytest.raises(comfy.PreflightError) as exc:
            comfy.cmd_submit(server.url, p)
        assert "MadeUpNode" in str(exc.value)
        assert "submitted" not in server.routes  # never POSTed

    def test_preflight_rejects_unknown_checkpoint(self, comfy, server, tmp_path):
        wf = {
            "1": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "missing.safetensors"},
            },
        }
        p = tmp_path / "bad.json"
        p.write_text(json.dumps(wf))
        with pytest.raises(comfy.PreflightError) as exc:
            comfy.cmd_submit(server.url, p)
        msg = str(exc.value)
        assert "missing.safetensors" in msg
        assert "submitted" not in server.routes


# ---------------------------------------------------------------------------
# wait — polling fallback path (no WS server)
# ---------------------------------------------------------------------------


class TestWaitPolling:
    def test_returns_done_when_history_completes(self, comfy, server):
        prompt_id = "p1"
        # Drip-feed: history empty, then completes after one poll.
        def populate():
            time.sleep(0.2)
            server.routes["history"][prompt_id] = {
                "status": {"completed": True, "messages": []},
                "outputs": {"4": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}},
            }
        threading.Thread(target=populate, daemon=True).start()

        result = comfy.cmd_wait(server.url, prompt_id, timeout=5.0, poll_only=True, poll_interval=0.1)
        assert result["status"] == "done"
        assert "4" in result["outputs"]

    def test_timeout(self, comfy, server):
        result = comfy.cmd_wait(server.url, "nope", timeout=0.3, poll_only=True, poll_interval=0.1)
        assert result["status"] == "timeout"

    def test_error_status(self, comfy, server):
        prompt_id = "p2"
        server.routes["history"][prompt_id] = {
            "status": {
                "completed": False,
                "status_str": "error",
                "messages": [["execution_error", {"exception_message": "OOM"}]],
            },
            "outputs": {},
        }
        result = comfy.cmd_wait(server.url, prompt_id, timeout=1.0, poll_only=True, poll_interval=0.05)
        assert result["status"] == "error"
        assert "OOM" in result.get("error", "")


# ---------------------------------------------------------------------------
# wait — WebSocket path
# ---------------------------------------------------------------------------


class TestWaitWebSocket:
    def test_ws_happy_path_then_history(self, comfy, server):
        from websockets.sync.server import serve

        prompt_id = "wsp1"
        ws_port = _free_port()

        def handler(connection):
            time.sleep(0.05)
            connection.send(
                json.dumps({"type": "executing", "data": {"prompt_id": prompt_id, "node": "1"}})
            )
            connection.send(
                json.dumps({"type": "executing", "data": {"prompt_id": prompt_id, "node": None}})
            )
            time.sleep(0.05)

        ws_server = serve(handler, "127.0.0.1", ws_port)
        thread = threading.Thread(target=ws_server.serve_forever, daemon=True)
        thread.start()
        try:
            # Patch the WS URL by tricking _wait_via_ws via base_url substitution.
            # Strategy: stand up a parallel HTTP server listing this prompt as done,
            # but point the driver at the WS-only port for the URL substitution.
            # The driver derives ws://host:port from base_url, so the easiest is
            # to point base_url at the WS port and have the HTTP fake on the same
            # port — instead we test the components separately. Verify only that
            # the WS call returns and consults history correctly.
            server.routes["history"][prompt_id] = {
                "status": {"completed": True, "messages": []},
                "outputs": {},
            }
            # Force the WS URL to our ws_port while history comes from `server`.
            base = server.url.replace(f":{server.port}", f":{ws_port}")
            # _wait_via_ws will hit ws://127.0.0.1:{ws_port} for WS, then HTTP on the same
            # base — that HTTP doesn't exist, so swap _history. Easier: call cmd_wait with
            # a hybrid: monkey-patch _history.
            orig_history = comfy._history
            comfy._history = lambda url, pid: orig_history(server.url, pid)
            try:
                result = comfy.cmd_wait(base, prompt_id, timeout=3.0, client_id="cid")
            finally:
                comfy._history = orig_history
            assert result["status"] == "done"
            assert result["prompt_id"] == prompt_id
        finally:
            ws_server.shutdown()
            thread.join(timeout=2)

    def test_ws_failure_falls_back_to_polling(self, comfy, server):
        prompt_id = "fb1"
        server.routes["history"][prompt_id] = {
            "status": {"completed": True, "messages": []},
            "outputs": {},
        }
        # No WS server is running on this URL, so _wait_via_ws raises and the
        # function falls back to polling — which finds the populated history.
        result = comfy.cmd_wait(server.url, prompt_id, timeout=2.0, client_id="cid", poll_interval=0.05)
        assert result["status"] == "done"


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------


class TestFetch:
    def test_downloads_outputs(self, comfy, server, tmp_path):
        prompt_id = "p3"
        server.routes["history"][prompt_id] = {
            "status": {"completed": True, "messages": []},
            "outputs": {
                "4": {
                    "images": [
                        {"filename": "out.png", "subfolder": "", "type": "output"},
                    ],
                },
            },
        }
        server.routes["view_files"]["out.png"] = b"\x89PNG\r\n\x1a\nfake"
        out_dir = tmp_path / "outputs"
        result = comfy.cmd_fetch(server.url, prompt_id, out_dir)
        assert len(result["files"]) == 1
        f = result["files"][0]
        assert Path(f["path"]).read_bytes() == b"\x89PNG\r\n\x1a\nfake"
        assert f["node"] == "4"

    def test_missing_history_raises(self, comfy, server, tmp_path):
        with pytest.raises(comfy.DriverError):
            comfy.cmd_fetch(server.url, "ghost", tmp_path)


# ---------------------------------------------------------------------------
# run = submit + wait + fetch
# ---------------------------------------------------------------------------


class TestRun:
    def test_chains_three(self, comfy, server, workflow_path, tmp_path):
        prompt_id = "test-id-1"
        server.routes["history"][prompt_id] = {
            "status": {"completed": True, "messages": []},
            "outputs": {
                "4": {
                    "images": [
                        {"filename": "img.png", "subfolder": "", "type": "output"},
                    ],
                },
            },
        }
        server.routes["view_files"]["img.png"] = b"\x89PNG\r\n\x1a\ndata"
        out_dir = tmp_path / "out"
        result = comfy.cmd_run(server.url, workflow_path, out_dir, timeout=2.0, poll_only=True, poll_interval=0.05)
        assert result["status"] == "done"
        assert result["prompt_id"] == prompt_id
        assert len(result["files"]) == 1


# ---------------------------------------------------------------------------
# queue / cancel / history
# ---------------------------------------------------------------------------


class TestQueueOps:
    def test_queue_returns_running_and_pending(self, comfy, server):
        server.routes["queue"] = {
            "queue_running": [[1, "id-r"]],
            "queue_pending": [[2, "id-p"]],
        }
        result = comfy.cmd_queue(server.url)
        assert result["running"] == [[1, "id-r"]]
        assert result["pending"] == [[2, "id-p"]]

    def test_cancel_pending_uses_delete(self, comfy, server):
        server.routes["queue"] = {
            "queue_running": [],
            "queue_pending": [[2, "pending-id"]],
        }
        comfy.cmd_cancel(server.url, "pending-id")
        action = server.routes["queue_actions"][0]
        assert action == {"delete": ["pending-id"]}

    def test_cancel_running_uses_interrupt(self, comfy, server):
        server.routes["queue"] = {
            "queue_running": [[1, "running-id"]],
            "queue_pending": [],
        }
        comfy.cmd_cancel(server.url, "running-id")
        assert server.routes.get("interrupt_calls") == [True]


# ---------------------------------------------------------------------------
# CLI surface — invoke once as a subprocess to confirm argparse + JSON stdout.
# ---------------------------------------------------------------------------


class TestCLISurface:
    def test_models_subcommand_emits_json(self, server):
        result = subprocess.run(
            [sys.executable, str(COMFY_PATH), "models"],
            env={"COMFYUI_URL": server.url, "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert "checkpoints" in payload

    def test_connection_refused_emits_actionable_hint(self, tmp_path):
        # Use a port that nothing's listening on.
        port = _free_port()
        result = subprocess.run(
            [sys.executable, str(COMFY_PATH), "models"],
            env={"COMFYUI_URL": f"http://127.0.0.1:{port}", "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert "error" in payload
        assert "comfyui" in payload.get("hint", "").lower()
