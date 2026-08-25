"""_query_still_matches and _backend_start_still_matches guard terminate_backend's
re-check-before-acting step -- stronger than cancel_backend's, since a wrong guess here
drops a whole connection, not just one query. Same reasoning as test_cancel_query_safety.py.
"""

from app.agents.tools.mcp.rds.remediation_mcp_server import _backend_start_still_matches, _query_still_matches


def test_query_matches_identical_query():
    assert _query_still_matches("SELECT 1", "SELECT 1") is True


def test_query_does_not_match_a_different_query():
    assert _query_still_matches("SELECT 1", "UPDATE orders SET status = 'shipped'") is False


def test_query_does_not_match_when_pid_has_no_current_query():
    assert _query_still_matches(None, "SELECT 1") is False


def test_backend_start_matches_identical_timestamp():
    assert _backend_start_still_matches(1787660153.862308, 1787660153.862308) is True


def test_backend_start_tolerates_tiny_float_round_trip_imprecision():
    # The epoch-seconds representation round-trips through JSON over MCP -- this
    # shouldn't cause a false negative for the same connection instance.
    assert _backend_start_still_matches(1787660153.8623081, 1787660153.8623079) is True


def test_backend_start_does_not_match_a_different_connection():
    # Same pid, but a genuinely different connection's start time -- the pid was reused.
    assert _backend_start_still_matches(1787660153.862308, 1787661000.0) is False


def test_backend_start_does_not_match_when_pid_has_no_current_backend_start():
    assert _backend_start_still_matches(None, 1787660153.862308) is False
