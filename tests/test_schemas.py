from src.tools.schemas import get_tool_schemas


def test_returns_eight_schemas():
    schemas = get_tool_schemas()
    assert len(schemas) == 8


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
    assert names == {"glob", "grep", "read", "edit", "write", "bash", "load_skill", "delegate"}
