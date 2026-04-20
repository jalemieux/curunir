from src.tools.schemas import get_tool_schemas


def test_returns_eleven_schemas():
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
    assert names == {"glob", "grep", "read", "edit", "write", "bash", "load_skill", "web_fetch", "delegate", "schedule", "run_skill"}


def test_run_skill_schema_registered():
    from src.tools.schemas import ALL_TOOL_SCHEMAS
    assert "run_skill" in ALL_TOOL_SCHEMAS
    schema = ALL_TOOL_SCHEMAS["run_skill"]["function"]
    required = schema["parameters"]["required"]
    assert set(required) == {"skill", "task", "intent"}
    props = schema["parameters"]["properties"]
    assert "skill" in props and "task" in props and "intent" in props


def test_run_skill_is_default_tool():
    from src.tools.schemas import get_tool_schemas
    default_names = {s["function"]["name"] for s in get_tool_schemas()}
    assert "run_skill" in default_names


def test_filter_by_names():
    schemas = get_tool_schemas(names=["read", "bash"])
    names = {s["function"]["name"] for s in schemas}
    assert names == {"read", "bash"}


def test_filter_ignores_unknown_names():
    schemas = get_tool_schemas(names=["read", "nonexistent"])
    names = {s["function"]["name"] for s in schemas}
    assert names == {"read"}


def test_delegate_schema_has_agent_param():
    from src.tools.schemas import ALL_TOOL_SCHEMAS
    delegate = ALL_TOOL_SCHEMAS["delegate"]
    props = delegate["function"]["parameters"]["properties"]
    required = delegate["function"]["parameters"]["required"]
    assert "agent" in props
    assert props["agent"]["type"] == "string"
    assert "agent" in required
    assert "task" in props and "task" in required
    assert "intent" in props and "intent" in required
