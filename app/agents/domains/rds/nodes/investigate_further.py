import json
import logging

from langchain.agents import create_agent
from langchain_mcp_adapters.client import MultiServerMCPClient

from app.agents.shared.state.agent import AgentState
from config.llm import get_llm
from config.mcp import stdio_server

logger = logging.getLogger(__name__)

MCP_SERVERS = {"rds": stdio_server("app.agents.domains.rds.mcp_server")}

_INVESTIGATION_PROMPT = (
    "You're investigating an RDS alarm for the '{environment}' environment. You already have "
    "the alarm payload, cluster status (context.cluster_info, including the writer instance's "
    "identifier under 'members'), recent metric trend, connection counts, and lock waits. "
    "Decide whether deeper investigation is warranted using the tools available:\n"
    "- get_performance_insights_top_sql: which SQL is consuming the most DB load recently -- "
    "pass the writer instance's identifier from context.cluster_info.members\n"
    "- explain_query_for_pid: get the query plan for a specific backend PID you've already seen "
    "(always pass environment='{environment}' when calling this)\n"
    "If nothing looks abnormal, don't call any tool -- say so directly. Otherwise, summarize "
    "what you found in a few sentences. Don't speculate beyond what the tools actually returned."
)


async def investigate_further(state: AgentState) -> dict:
    client = MultiServerMCPClient(MCP_SERVERS)
    tools = await client.get_tools()
    expensive_tools = [t for t in tools if t.name in ("get_performance_insights_top_sql", "explain_query_for_pid")]

    environment = state["context"]["environment"]
    agent = create_agent(
        get_llm(), expensive_tools, system_prompt=_INVESTIGATION_PROMPT.format(environment=environment)
    )
    input_summary = json.dumps({"alarm": state["raw_event"]["payload"], "context": state["context"]}, default=str)
    result = await agent.ainvoke({"messages": [{"role": "user", "content": input_summary}]})

    tools_called = [call["name"] for msg in result["messages"] for call in getattr(msg, "tool_calls", [])]
    logger.info(
        "raw_event_id=%s environment=%s tools_called=%s",
        state["raw_event"]["id"],
        environment,
        tools_called or "none",
    )

    return {"investigation": result["messages"][-1].content}
