"""Tests for the comfyui-workflows /object_info helper script."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).parent.parent / "skills" / "comfyui-workflows"
HELPER = SKILL_ROOT / "scripts" / "fetch_object_info.py"


def _load_helper():
    """Load the helper script as a module by file path (it's not on sys.path)."""
    spec = importlib.util.spec_from_file_location("comfyui_fetch_object_info", HELPER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def helper():
    assert HELPER.is_file(), "fetch_object_info.py must exist"
    return _load_helper()


# Realistic /object_info shape: a top-level object keyed by node class name,
# each with input/output schema. Keep this fixture small but representative.
RAW_OBJECT_INFO = {
    "KSampler": {
        "input": {
            "required": {
                "model": ["MODEL"],
                "seed": ["INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}],
                "steps": ["INT", {"default": 20, "min": 1, "max": 10000}],
                "cfg": ["FLOAT", {"default": 8.0, "min": 0.0, "max": 100.0, "step": 0.1}],
                "sampler_name": [["euler", "dpmpp_2m"], {"default": "euler"}],
                "scheduler": [["normal", "karras"]],
                "positive": ["CONDITIONING"],
                "negative": ["CONDITIONING"],
                "latent_image": ["LATENT"],
                "denoise": ["FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0}],
            },
            "optional": {},
        },
        "output": ["LATENT"],
        "output_name": ["LATENT"],
        "name": "KSampler",
        "display_name": "KSampler",
        "description": "samples",
        "category": "sampling",
    },
    "CustomThirdPartyNode": {
        "input": {
            "required": {
                "image": ["IMAGE"],
                "scale": ["FLOAT", {"default": 1.5}],
            },
        },
        "output": ["IMAGE"],
        "output_name": ["IMAGE"],
        "name": "CustomThirdPartyNode",
        "display_name": "Fancy Custom",
        "description": "third party",
        "category": "custom_nodes/fancy",
    },
}


class TestNormalize:
    def test_normalize_returns_dict_keyed_by_class(self, helper):
        out = helper.normalize(RAW_OBJECT_INFO)
        assert set(out.keys()) == {"KSampler", "CustomThirdPartyNode"}

    def test_normalize_keeps_inputs_and_outputs(self, helper):
        out = helper.normalize(RAW_OBJECT_INFO)
        ksampler = out["KSampler"]
        assert "inputs" in ksampler
        assert "outputs" in ksampler
        assert ksampler["category"] == "sampling"
        assert ksampler["display_name"] == "KSampler"

    def test_normalize_collapses_required_and_optional(self, helper):
        out = helper.normalize(RAW_OBJECT_INFO)
        inputs = out["KSampler"]["inputs"]
        assert "model" in inputs
        assert "seed" in inputs
        # required/optional flag is preserved on each input.
        assert inputs["model"]["required"] is True
        assert inputs["seed"]["type"] == "INT"
        assert inputs["seed"]["default"] == 0

    def test_normalize_handles_enum_inputs(self, helper):
        out = helper.normalize(RAW_OBJECT_INFO)
        sampler = out["KSampler"]["inputs"]["sampler_name"]
        # Enum / list-of-strings type is the literal list, with choices recorded.
        assert sampler["type"] == "ENUM"
        assert sampler["choices"] == ["euler", "dpmpp_2m"]
        assert sampler["default"] == "euler"

    def test_normalize_works_for_custom_node(self, helper):
        out = helper.normalize(RAW_OBJECT_INFO)
        node = out["CustomThirdPartyNode"]
        assert node["inputs"]["image"]["type"] == "IMAGE"
        assert node["inputs"]["scale"]["default"] == 1.5
        assert node["outputs"] == ["IMAGE"]
        assert node["category"] == "custom_nodes/fancy"

    def test_normalize_optional_inputs_marked_not_required(self, helper):
        raw = {
            "X": {
                "input": {
                    "required": {"a": ["INT"]},
                    "optional": {"b": ["STRING", {"default": ""}]},
                },
                "output": ["INT"],
                "output_name": ["INT"],
                "name": "X",
                "display_name": "X",
                "description": "",
                "category": "test",
            }
        }
        out = helper.normalize(raw)
        assert out["X"]["inputs"]["a"]["required"] is True
        assert out["X"]["inputs"]["b"]["required"] is False


class TestFilterAndCli:
    def test_normalize_filter_by_class_names(self, helper):
        out = helper.normalize(RAW_OBJECT_INFO, classes=["KSampler"])
        assert list(out.keys()) == ["KSampler"]

    def test_normalize_filter_unknown_class_returns_empty(self, helper):
        out = helper.normalize(RAW_OBJECT_INFO, classes=["NoSuchNode"])
        assert out == {}

    def test_default_url_is_local_comfyui(self, helper):
        assert helper.DEFAULT_URL == "http://127.0.0.1:8188"

    def test_env_override_for_url(self, monkeypatch, helper):
        monkeypatch.setenv("COMFYUI_URL", "http://example.com:9000")
        assert helper.resolve_url() == "http://example.com:9000"

    def test_resolve_url_default(self, monkeypatch, helper):
        monkeypatch.delenv("COMFYUI_URL", raising=False)
        assert helper.resolve_url() == helper.DEFAULT_URL


class TestFetchObjectInfo:
    def test_fetch_calls_object_info_endpoint(self, helper, monkeypatch):
        captured = {}

        class FakeResponse:
            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        def fake_get(url, timeout):
            captured["url"] = url
            captured["timeout"] = timeout
            return FakeResponse(RAW_OBJECT_INFO)

        monkeypatch.setattr(helper.httpx, "get", fake_get)
        result = helper.fetch_object_info("http://127.0.0.1:8188")
        assert captured["url"] == "http://127.0.0.1:8188/object_info"
        assert result == RAW_OBJECT_INFO


class TestEndToEndCli:
    def test_main_writes_normalized_json(self, helper, monkeypatch, tmp_path):
        out_path = tmp_path / "object_info.json"

        def fake_fetch(url):
            return RAW_OBJECT_INFO

        monkeypatch.setattr(helper, "fetch_object_info", fake_fetch)
        rc = helper.main(["--out", str(out_path)])
        assert rc == 0
        assert out_path.is_file()
        data = json.loads(out_path.read_text())
        assert "KSampler" in data
        assert data["KSampler"]["inputs"]["seed"]["type"] == "INT"

    def test_main_filter_classes(self, helper, monkeypatch, tmp_path):
        out_path = tmp_path / "filtered.json"
        monkeypatch.setattr(helper, "fetch_object_info", lambda url: RAW_OBJECT_INFO)
        rc = helper.main(["--out", str(out_path), "--classes", "CustomThirdPartyNode"])
        assert rc == 0
        data = json.loads(out_path.read_text())
        assert list(data.keys()) == ["CustomThirdPartyNode"]
