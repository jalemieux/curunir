from src.tools.schemas import (
    _DEFAULT_TOOL_NAMES,
    _OPT_IN_TOOL_NAMES,
    get_tool_schemas,
)


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


def test_to_audio_is_opt_in():
    assert "to_audio" in _OPT_IN_TOOL_NAMES
    assert "to_audio" not in _DEFAULT_TOOL_NAMES
    # Default schema list must not surface opt-in tools.
    default_names = {s["function"]["name"] for s in get_tool_schemas()}
    assert "to_audio" not in default_names
    # But callers can opt in by name.
    opt_in = {s["function"]["name"] for s in get_tool_schemas(names=["to_audio"])}
    assert opt_in == {"to_audio"}


def test_to_audio_schema_shape():
    schemas = get_tool_schemas(names=["to_audio"])
    assert len(schemas) == 1
    params = schemas[0]["function"]["parameters"]
    assert params["required"] == ["content"]
    assert set(params["properties"].keys()) == {"content", "voice", "model", "filename"}
