from src.tools.skill_tool import exec_load_skill


class TestExecLoadSkill:
    def test_loads_existing_skill(self, tmp_skills, agent_config):
        skill_dir = tmp_skills / "research"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Research\nDo research things.")
        result = exec_load_skill({"name": "research"}, agent_config)
        assert "# Research" in result

    def test_missing_skill(self, agent_config):
        result = exec_load_skill({"name": "nonexistent"}, agent_config)
        assert "not found" in result.lower()
