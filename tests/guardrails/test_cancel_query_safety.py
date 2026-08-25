"""_query_still_matches guards cancel_backend's re-check-before-acting step: by the time
a human approves a remediation, the pid may have been reused by an unrelated, newer
backend. These tests exist so that TOCTOU guard can't regress silently, same reasoning
as test_explain_query_safety.py.
"""

from app.agents.tools.mcp.rds.remediation_mcp_server import _query_still_matches


def test_matches_identical_query():
    assert _query_still_matches("SELECT * FROM orders WHERE id = 1", "SELECT * FROM orders WHERE id = 1") is True


def test_does_not_match_a_different_query():
    assert _query_still_matches("SELECT 1", "SELECT * FROM orders WHERE id = 1") is False


def test_does_not_match_when_pid_has_no_current_query():
    # pg_stat_activity returned no row for this pid at all -- represented as None.
    assert _query_still_matches(None, "SELECT * FROM orders WHERE id = 1") is False


def test_matches_when_both_sides_are_truncated_the_same_way():
    # get_long_running_queries truncates to left(query, 200) at proposal time; the
    # re-check truncates the freshly-read query the same way before comparing.
    long_query = "SELECT * FROM orders WHERE " + "x = 1 AND " * 50
    assert _query_still_matches(long_query, long_query[:200]) is True
