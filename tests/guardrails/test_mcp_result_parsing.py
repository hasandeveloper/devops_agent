"""parse_mcp_result is where a real bug shipped earlier this project's history --
it assumed every MCP tool's text was JSON-encoded, and broke on a bare-string return.
These tests exist so that regression can't happen silently again. Used by both
gather_context.py (direct tool calls) and investigate_further.py (ToolMessage content
from the ReAct agent's own tool calls) -- same MCP text-block shape either way.

parse_mcp_list_result guards a second, related bug that shipped later: a list[dict]
tool with exactly one result collapses to a bare dict under parse_mcp_result alone
(confirmed live -- get_idle_in_transaction_connections with one candidate broke
propose_idle_connection_remediation.py's list comprehension this way in production).
"""

from config.mcp import parse_mcp_list_result, parse_mcp_result


def test_unwraps_single_json_encoded_block():
    result = [{"type": "text", "text": '{"status": "ok"}'}]
    assert parse_mcp_result(result) == {"status": "ok"}


def test_falls_back_to_raw_text_for_a_bare_string_block():
    # FastMCP doesn't JSON-encode primitive string returns -- 'dev', not '"dev"'.
    result = [{"type": "text", "text": "dev"}]
    assert parse_mcp_result(result) == "dev"


def test_parses_multiple_blocks_into_a_list():
    result = [{"type": "text", "text": "1"}, {"type": "text", "text": "not json"}]
    assert parse_mcp_result(result) == [1, "not json"]


def test_passes_through_non_mcp_shaped_input_unchanged():
    assert parse_mcp_result({"already": "a dict"}) == {"already": "a dict"}
    assert parse_mcp_result([]) == []
    assert parse_mcp_result([1, 2, 3]) == [1, 2, 3]


def test_list_result_wraps_a_single_collapsed_dict_back_into_a_list():
    # The exact bug: a list[dict]-returning tool with exactly one row collapses to a
    # bare dict under parse_mcp_result, indistinguishable at the wire level from a tool
    # that legitimately returns a bare dict.
    result = [{"type": "text", "text": '{"pid": 123, "duration_seconds": 45.6}'}]
    assert parse_mcp_list_result(result) == [{"pid": 123, "duration_seconds": 45.6}]


def test_list_result_passes_through_multiple_items_unchanged():
    result = [{"type": "text", "text": '{"pid": 1}'}, {"type": "text", "text": '{"pid": 2}'}]
    assert parse_mcp_list_result(result) == [{"pid": 1}, {"pid": 2}]


def test_list_result_passes_through_zero_items_unchanged():
    assert parse_mcp_list_result([]) == []
