# Security Policy

## Supported Versions

This project is developed on a single `main` branch, not released under
semantic versioning -- security fixes are applied to the latest commit on
`main` rather than backported to older tags.

## Reporting a Vulnerability

Please do **not** open a public GitHub issue for security vulnerabilities.

Instead, report it privately by emailing **hasanalitech@gmail.com** with:

- A description of the vulnerability and its potential impact
- Steps to reproduce (a minimal repro is very helpful)
- Any relevant logs or stack traces (with secrets redacted)

You should expect an initial response within a few days. Once a fix is
confirmed, we'll coordinate on disclosure timing before any public writeup.

## Design Notes for Reviewers

A few things worth knowing if you're auditing this codebase:

- The RDS MCP server (`app/agents/tools/mcp/rds/mcp_server.py`) is
  deliberately read-only -- every tool exposed to the LLM only calls
  read-only AWS APIs (`describe_*`, `get_*`, `list_tags_for_resource`) or
  runs `SELECT`/`EXPLAIN` against a dedicated Postgres role
  (`devops_agent_readonly`) that holds no write/DDL/superuser privileges.
  There is currently no code path in this project that can mutate or
  restart infrastructure.
- `explain_query_for_pid`'s `_explain_safety_violation()` guards against SQL
  injection into an `EXPLAIN` statement built via string interpolation --
  it rejects anything but a single `SELECT`/`WITH` statement before that
  text ever reaches `execute()`. See its docstring for why this exists (a
  stacked-query risk under psycopg's simple query protocol).
- Real credentials (`.env`, `~/.aws`) are never committed -- `.env` is
  gitignored and `.env.example` documents every variable without values.
  If you find a secret committed to history, please report it the same way
  as a vulnerability above.
