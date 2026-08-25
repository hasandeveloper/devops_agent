import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_mcp_result(result):
    """MCP tool results arrive as a list of {"type": "text", "text": "..."} blocks --
    one block per returned item. Unwrap to the plain Python value the tool actually returned.

    A dict/list-returning tool's text is JSON-encoded (e.g. '{"status": "ok"}'); a plain
    str-returning tool's text is the bare string itself (e.g. "dev", not '"dev"') -- confirmed
    empirically, FastMCP doesn't JSON-encode primitive string returns. json.loads fails on the
    latter, so fall back to the raw text rather than assuming every tool's output is JSON.

    Also what a ToolMessage.content looks like when a ReAct agent calls an MCP tool on its
    own (see investigate_further.py) -- same shape, same unwrap logic applies there too.
    """

    def _parse_one(text):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    if isinstance(result, list) and result and all(isinstance(item, dict) and "text" in item for item in result):
        parsed = [_parse_one(item["text"]) for item in result]
        return parsed[0] if len(parsed) == 1 else parsed
    return result


def parse_mcp_list_result(result) -> list:
    """Like parse_mcp_result, but for a tool whose return type is documented as a list
    (list[dict], usually) -- use this instead of parse_mcp_result directly wherever the
    caller always expects a list back, e.g. `for c in parse_mcp_list_result(...)`.

    parse_mcp_result's own unwrap (`parsed[0] if len(parsed) == 1 else parsed`) collapses
    a single-item list down to its bare element, because at the wire level a tool
    returning `[{"pid": 1}]` and one returning `{"pid": 1}` produce the exact same single
    content block -- there is no way to tell them apart after the fact from parse_mcp_result
    alone. Confirmed empirically: get_idle_in_transaction_connections with exactly one
    candidate returns a bare dict, not a one-element list, which breaks any caller written
    to always iterate a list (e.g. `[c for c in idle_connections if ...]` iterates a dict's
    *keys*, all strings, instead of its rows). The zero-item and many-item cases were
    already correct by construction; only the exactly-one-item case needed this.
    """
    parsed = parse_mcp_result(result)
    return parsed if isinstance(parsed, list) else [parsed]


def stdio_server(module_path: str) -> dict:
    """Connection config for an MCP server run as `python -m <module_path>`.

    Uses sys.executable (the running venv's interpreter) and an explicit cwd
    of the project root, so this resolves correctly regardless of where the
    calling process (worker.py, tests, etc.) was itself launched from.

    Every MCP server in this codebase is only ever spawned by worker-side pipeline
    code (gather_context.py, investigate_further.py), never by the FastAPI process --
    so its own logging (see config/logging.py's LOG_FILE_NAME handling) is routed to
    jobs.log, matching where the rest of that pipeline's logs already go. {**os.environ,
    ...} rather than a bare override dict: passing "env" at all replaces the
    subprocess's entire environment, so omitting the parent's env here would silently
    break AWS credential discovery, DB env vars, PATH, everything.
    """
    return {
        "command": sys.executable,
        "args": ["-m", module_path],
        "transport": "stdio",
        "cwd": str(PROJECT_ROOT),
        "env": {**os.environ, "LOG_FILE_NAME": "jobs.log"},
    }
