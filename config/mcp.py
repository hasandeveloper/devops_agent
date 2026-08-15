import asyncio
import os
import sys
from pathlib import Path

from langchain_core.tools import BaseTool

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Every MCP tool call is a round trip over stdio to mcp_server.py, which can hang on
# things outside our control -- a stalled AWS API call, a wedged DB connection (the
# DB-internal tools use psycopg directly with no connect_timeout set). Without a
# timeout, one hung tool call blocks the whole diagnosis pipeline, and since Celery
# has no per-task timeout configured either, that worker slot stays stuck
# indefinitely. Use invoke_tool()/with_timeout() below wherever a tool gets called,
# instead of calling .ainvoke() directly.
MCP_TOOL_TIMEOUT_SECONDS = 30


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


async def invoke_tool(tool: BaseTool, args: dict):
    """Call an MCP tool directly (outside an agent loop), bounded by MCP_TOOL_TIMEOUT_SECONDS.

    Use this for tools your own code calls explicitly, e.g. gather_context.py's fixed
    sequence of lookups. Raises asyncio.TimeoutError if the call doesn't finish in
    time, which propagates like any other unexpected exception (see
    jobs/webhooks_job.py's retry handling).
    """
    return await asyncio.wait_for(tool.ainvoke(args), timeout=MCP_TOOL_TIMEOUT_SECONDS)


def with_timeout(tool: BaseTool) -> BaseTool:
    """Wrap a tool so an agent loop (e.g. create_agent's ReAct loop) can't hang on it.

    When an LLM decides which tools to call and when, our code never calls .ainvoke()
    directly -- the agent framework does, internally. To still bound how long any one
    of those calls can run, wrap the tool's own coroutine before handing it to
    create_agent, e.g. `create_agent(llm, [with_timeout(t) for t in tools], ...)`.
    """
    original_coroutine = tool.coroutine

    async def _bounded(*args, **kwargs):
        return await asyncio.wait_for(original_coroutine(*args, **kwargs), timeout=MCP_TOOL_TIMEOUT_SECONDS)

    return tool.model_copy(update={"coroutine": _bounded})
