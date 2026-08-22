import asyncio

from langchain_core.tools import BaseTool

# Every MCP tool call is a round trip over stdio to mcp_server.py, which can hang on
# things outside our control -- a stalled AWS API call, a wedged DB connection (the
# DB-internal tools use psycopg directly with no connect_timeout set). Without a
# timeout, one hung tool call blocks the whole diagnosis pipeline, and since Celery
# has no per-task timeout configured either, that worker slot stays stuck
# indefinitely. Use invoke_tool()/with_timeout() below wherever a tool gets called,
# instead of calling .ainvoke() directly.
#
# Was 30s; raised after measuring psycopg.connect() itself (not the query -- the
# lock_waits query runs in ~0.2s once connected) taking anywhere from ~1.4s to 35.8s
# against the real dev Aurora cluster across repeated attempts, for reasons that
# didn't trace to SSL negotiation, IPv6, or DNS (all checked and ruled out) --
# apparently just network/connection-setup variance on this path. 30s was regularly
# tripped by connection setup alone, before any query ran. This is a stopgap for that
# observed variance, not a fix for its root cause.
MCP_TOOL_TIMEOUT_SECONDS = 60


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
