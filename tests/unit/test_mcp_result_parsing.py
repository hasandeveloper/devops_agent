"""_parse_mcp_result is where a real bug shipped earlier this project's history --
it assumed every MCP tool's text was JSON-encoded, and broke on a bare-string return.
These tests exist so that regression can't happen silently again.
"""

from app.agents.domains.rds.nodes.gather_context import _parse_mcp_result


def test_unwraps_single_json_encoded_block():
    result = [{"type": "text", "text": '{"status": "ok"}'}]
    assert _parse_mcp_result(result) == {"status": "ok"}


def test_falls_back_to_raw_text_for_a_bare_string_block():
    # FastMCP doesn't JSON-encode primitive string returns -- 'dev', not '"dev"'.
    result = [{"type": "text", "text": "dev"}]
    assert _parse_mcp_result(result) == "dev"


def test_parses_multiple_blocks_into_a_list():
    result = [{"type": "text", "text": "1"}, {"type": "text", "text": "not json"}]
    assert _parse_mcp_result(result) == [1, "not json"]


def test_passes_through_non_mcp_shaped_input_unchanged():
    assert _parse_mcp_result({"already": "a dict"}) == {"already": "a dict"}
    assert _parse_mcp_result([]) == []
    assert _parse_mcp_result([1, 2, 3]) == [1, 2, 3]
