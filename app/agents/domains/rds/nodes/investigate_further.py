import json
import logging

from langchain.agents import create_agent
from langchain_core.messages import ToolMessage
from langchain_mcp_adapters.client import MultiServerMCPClient

from app.agents.shared.state.agent import AgentState
from app.prompts.rds.investigation import build_prompt
from config.llm import get_llm
from config.mcp import parse_mcp_result, stdio_server
from config.reliability.mcp_timeouts import with_timeout
from config.reliability.token_budget import TokenBudgetTracker
from config.settings import settings

logger = logging.getLogger(__name__)

MCP_SERVERS = {"rds": stdio_server("app.agents.tools.mcp.rds.mcp_server")}

# create_agent builds a small internal LangGraph graph that alternates between an
# "agent" step (LLM decides what to do) and a "tools" step (runs whatever it called).
# recursion_limit caps the total number of those steps, not the number of tool calls
# directly -- each round trip (agent decides -> tool runs) costs 2 steps. Without this,
# nothing stops a confusing alarm from making the LLM call tools indefinitely, which
# costs real money and can stall the Celery task that's running this. 11 steps allows
# up to 5 tool-call round trips plus one final answer -- generous for the 4 available
# tools (one round trip each, plus room to follow up), but not unbounded. If this limit
# is ever hit, LangGraph raises GraphRecursionError, which propagates up like any other
# unexpected exception (see jobs/webhooks_job.py's retry handling).
_MAX_INVESTIGATION_STEPS = 11


def _extract_query_evidence(messages: list) -> list[dict]:
    """Pull the exact SQL text out of this investigation's tool calls, straight from the
    MCP tool results -- not the LLM's paraphrase of them. Posted to Slack alongside the
    diagnosis so "which query is actually causing this" doesn't require re-querying AWS
    by hand after the fact.

    Deliberately narrow: only the single highest-load statement from
    get_performance_insights_top_sql (that tool already returns up to 10, sorted by
    load -- showing all of them would make the Slack message unreadable), and only the
    query text from explain_query_for_pid, never its full JSON query plan (large,
    deeply nested, not something you can usefully skim in a Slack message).
    """
    evidence = []
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        result = parse_mcp_result(msg.content)
        if msg.name == "get_performance_insights_top_sql" and result:
            top = result[0] if isinstance(result, list) else result
            if top.get("statement"):
                evidence.append({"tool": msg.name, "query": top["statement"]})
        elif msg.name == "explain_query_for_pid" and isinstance(result, dict) and result.get("query"):
            evidence.append({"tool": msg.name, "query": result["query"]})
    return evidence


async def investigate_further(state: AgentState) -> dict:
    client = MultiServerMCPClient(MCP_SERVERS)
    tools = await client.get_tools()
    # with_timeout: the LLM decides if/when to call these, not our own code, so a
    # timeout has to be baked into the tool itself rather than wrapped around a
    # .ainvoke() call we control (see config/mcp.py).
    expensive_tools = [
        with_timeout(t)
        for t in tools
        if t.name
        in ("get_performance_insights_top_sql", "explain_query_for_pid", "get_replica_lag", "get_table_bloat")
    ]

    environment = state["context"]["environment"]
    agent = create_agent(get_llm(), expensive_tools, system_prompt=build_prompt(environment))
    input_summary = json.dumps({"alarm": state["raw_event"]["payload"], "context": state["context"]}, default=str)
    token_tracker = TokenBudgetTracker(max_tokens=settings.max_investigation_tokens)
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": input_summary}]},
        config={"recursion_limit": _MAX_INVESTIGATION_STEPS, "callbacks": [token_tracker]},
    )
    # Checked after the loop, not during -- see TokenBudgetTracker's docstring for why
    # it can't interrupt the loop mid-flight. This still stops an unusually expensive
    # investigation from reaching diagnose/persist_incident/notify_slack, and (being
    # classified as non-retryable in jobs/webhooks_job.py) from burning the same
    # budget again on a retry.
    token_tracker.check()

    tools_called = [call["name"] for msg in result["messages"] for call in getattr(msg, "tool_calls", [])]
    query_evidence = _extract_query_evidence(result["messages"])
    logger.info(
        "raw_event_id=%s environment=%s tools_called=%s tokens_used=%d query_evidence=%d",
        state["raw_event"]["id"],
        environment,
        tools_called or "none",
        token_tracker.total_tokens,
        len(query_evidence),
    )

    return {"investigation": result["messages"][-1].content, "query_evidence": query_evidence}
