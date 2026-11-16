def test_resource_and_prompt_utilities_remain_without_webhook_events():
    from tools.mcp_tool_schema import _build_utility_schemas
    from tools.mcp_tool_registration import _UTILITY_HANDLER_FACTORIES

    names = {entry["handler_key"] for entry in _build_utility_schemas("example")}
    assert names == {"list_resources", "read_resource", "list_prompts", "get_prompt"}
    assert names == set(_UTILITY_HANDLER_FACTORIES)