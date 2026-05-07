"""Tests for ComfyUI skill bundled templates and skill discoverability."""
import json
from pathlib import Path

import pytest

from src.skills import load_registry, load_skill, parse_frontmatter

SKILL_ROOT = Path(__file__).parent.parent / "skills" / "comfyui-workflows"
TEMPLATE_DIR = SKILL_ROOT / "templates"
TEMPLATES = ["txt2img.json", "img2img.json", "upscale.json", "controlnet.json"]


def _load_template(name: str) -> dict:
    return json.loads((TEMPLATE_DIR / name).read_text())


class TestSkillDiscovery:
    def test_skill_md_exists(self):
        assert (SKILL_ROOT / "SKILL.md").is_file()

    def test_frontmatter_valid(self):
        fm = parse_frontmatter((SKILL_ROOT / "SKILL.md").read_text())
        assert fm.get("name") == "comfyui-workflows"
        assert fm.get("description"), "description must be present"

    def test_skill_appears_in_registry(self):
        registry = load_registry([SKILL_ROOT.parent])
        assert "comfyui-workflows" in registry

    def test_load_skill_returns_content(self):
        content = load_skill("comfyui-workflows", [SKILL_ROOT.parent])
        assert "Skill not found" not in content
        assert "comfyui-workflows" in content

    def test_references_exist(self):
        assert (SKILL_ROOT / "references" / "core_nodes.md").is_file()
        assert (SKILL_ROOT / "references" / "formats.md").is_file()


@pytest.mark.parametrize("name", TEMPLATES)
class TestTemplateValidJson:
    def test_template_file_exists(self, name):
        assert (TEMPLATE_DIR / name).is_file(), f"missing {name}"

    def test_template_parses_as_json(self, name):
        data = _load_template(name)
        assert isinstance(data, dict), "API /prompt format is a flat object keyed by node id"

    def test_template_keys_are_string_ids(self, name):
        data = _load_template(name)
        assert data, "template must have at least one node"
        for key in data:
            assert isinstance(key, str)
            int(key)  # node IDs in /prompt format are stringified ints

    def test_each_node_has_class_type_and_inputs(self, name):
        data = _load_template(name)
        for node_id, node in data.items():
            assert "class_type" in node, f"node {node_id} in {name} missing class_type"
            assert "inputs" in node, f"node {node_id} in {name} missing inputs"
            assert isinstance(node["inputs"], dict)
            assert isinstance(node["class_type"], str)

    def test_links_reference_existing_nodes(self, name):
        data = _load_template(name)
        for node_id, node in data.items():
            for input_name, value in node["inputs"].items():
                if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                    target_id, slot = value
                    assert target_id in data, (
                        f"{name}: node {node_id}.{input_name} links to "
                        f"missing node {target_id}"
                    )
                    assert isinstance(slot, int) and slot >= 0


class TestTemplateContents:
    def test_txt2img_has_required_node_classes(self):
        data = _load_template("txt2img.json")
        class_types = {node["class_type"] for node in data.values()}
        # Standard txt2img pipeline:
        for required in {
            "CheckpointLoaderSimple",
            "CLIPTextEncode",
            "EmptyLatentImage",
            "KSampler",
            "VAEDecode",
            "SaveImage",
        }:
            assert required in class_types, f"txt2img missing {required}"

    def test_img2img_uses_image_loader_and_vae_encode(self):
        data = _load_template("img2img.json")
        class_types = {node["class_type"] for node in data.values()}
        assert "LoadImage" in class_types
        assert "VAEEncode" in class_types
        assert "KSampler" in class_types

    def test_upscale_has_upscale_node(self):
        data = _load_template("upscale.json")
        class_types = {node["class_type"] for node in data.values()}
        # Either model-based or simple latent/image upscale should be present.
        assert any("Upscale" in c for c in class_types), (
            f"upscale template lacks any Upscale node, got {class_types}"
        )

    def test_controlnet_has_controlnet_apply(self):
        data = _load_template("controlnet.json")
        class_types = {node["class_type"] for node in data.values()}
        assert "ControlNetLoader" in class_types
        assert any("ControlNetApply" in c for c in class_types), (
            f"controlnet template lacks ControlNetApply node, got {class_types}"
        )
