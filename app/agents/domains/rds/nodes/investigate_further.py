import json
import logging

from langchain.agents import create_agent
from langchain_mcp_adapters.client import MultiServerMCPClient

from app.agents.shared.state.agent import AgentState
from app.prompts.rds.investigation import build_prompt
from config.llm import get_llm
from config.mcp import stdio_server

logger = logging.getLogger(__name__)

MCP_SERVERS = {"rds": stdio_server("app.agents.domains.rds.mcp_server")}


async def investigate_further(state: AgentState) -> dict:
    client = MultiServerMCPClient(MCP_SERVERS)
    tools = await client.get_tools()
    expensive_tools = [t for t in tools if t.name in ("get_performance_insights_top_sql", "explain_query_for_pid")]

    environment = state["context"]["environment"]
    agent = create_agent(get_llm(), expensive_tools, system_prompt=build_prompt(environment))
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
