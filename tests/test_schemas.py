import pytest

from src.tools.schemas import ALL_TOOL_SCHEMAS, get_tool_schemas


def test_returns_default_schemas():
    schemas = get_tool_schemas()
    assert len(schemas) == 11


def test_schema_format():
    for schema in get_tool_schemas():
        assert schema["type"] == "function"
        func = schema["function"]
        assert "name" in func
        assert "description" in func
        assert "parameters" in func
        assert func["parameters"]["type"] == "object"


def test_expected_tool_names():
    names = {s["function"]["name"] for s in get_tool_schemas()}
    assert names == {"glob", "grep", "read", "edit", "write", "bash", "load_skill", "web_fetch", "delegate", "schedule", "attach"}


def test_filter_by_names():
    schemas = get_tool_schemas(names=["read", "bash"])
    names = {s["function"]["name"] for s in schemas}
    assert names == {"read", "bash"}


def test_filter_ignores_unknown_names():
    schemas = get_tool_schemas(names=["read", "nonexistent"])
    names = {s["function"]["name"] for s in schemas}
    assert names == {"read"}


def test_bash_description_warns_against_agent_tool_names():
    """Bash description must explicitly tell the model not to type agent tool
    names (load_skill, web_fetch, delegate, schedule, attach) as shell commands.
    See issue #54: the agent occasionally typed `load_skill playwright` into
    bash, the shell returned 'command not found', and the failure was silent."""
    desc = ALL_TOOL_SCHEMAS["bash"]["function"]["description"]
    assert "shell" in desc.lower()
    assert "Do NOT" in desc or "do not" in desc.lower()
    for agent_tool in ("load_skill", "web_fetch", "delegate", "schedule", "attach"):
        assert agent_tool in desc, (
            f"bash description must name agent tool '{agent_tool}' so the "
            f"model knows not to type it as a shell command"
        )


@pytest.mark.parametrize(
    "tool_name", ["load_skill", "web_fetch", "delegate", "schedule", "attach"]
)
def test_agent_only_tool_descriptions_start_with_framing(tool_name):
    """Agent-only tools must lead with 'Agent tool' so the model has a
    consistent signal that these are tool-call-schema invocations, not
    shell binaries."""
    desc = ALL_TOOL_SCHEMAS[tool_name]["function"]["description"]
    assert desc.startswith("Agent tool"), (
        f"{tool_name} description must start with 'Agent tool', got: {desc[:60]!r}"
    )
