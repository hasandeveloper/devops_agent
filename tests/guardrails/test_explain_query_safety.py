"""_explain_safety_violation guards the one place in this codebase that interpolates
a string into a SQL statement rather than parameterizing it (mcp_server.py's
explain_query_for_pid). A real second-order SQL injection was found and fixed here
earlier in this project's history -- these tests exist so it can't regress silently.
"""

import pytest

from app.agents.tools.mcp.rds.mcp_server import _explain_safety_violation


@pytest.mark.parametrize("query", ["SELECT 1", "select * from foo", "WITH t AS (SELECT 1) SELECT * FROM t"])
def test_allows_single_select_or_with_statement(query):
    assert _explain_safety_violation(query) is None


def test_rejects_stacked_statements():
    # The exact attack this check exists for -- confirmed empirically elsewhere that
    # this genuinely creates the table if it ever reaches execute() unchecked.
    violation = _explain_safety_violation("SELECT 1; CREATE TABLE x (id int)")
    assert violation is not None
    assert "multi-statement" in violation


def test_rejects_trailing_semicolon_stacked_statement():
    violation = _explain_safety_violation("SELECT 1; SELECT 2;")
    assert violation is not None


def test_allows_single_trailing_semicolon():
    # rstrip(";") before counting -- one trailing semicolon on an otherwise single
    # statement is not a stacked-statement attack.
    assert _explain_safety_violation("SELECT 1;") is None


@pytest.mark.parametrize("query", ["DELETE FROM foo", "UPDATE foo SET x = 1", "DROP TABLE foo", "INSERT INTO foo VALUES (1)"])
def test_rejects_non_select_statements(query):
    violation = _explain_safety_violation(query)
    assert violation is not None
    assert "non-SELECT" in violation
