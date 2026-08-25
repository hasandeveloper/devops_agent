import logging
import uuid

from langchain_mcp_adapters.client import MultiServerMCPClient

from app.agents.shared.schema.remediation import RemediationProposal
from app.agents.shared.state.agent import AgentState
from app.prompts.rds.idle_connection_remediation import build_prompt
from app.services.remediation_service import action_to_dict, create_remediation_action, recompute_incident_status
from config.llm import get_llm
from config.mcp import parse_mcp_result, stdio_server
from config.reliability.mcp_timeouts import invoke_tool
from config.settings import settings
from db import SessionLocal

logger = logging.getLogger(__name__)

MCP_SERVERS = {"rds": stdio_server("app.agents.tools.mcp.rds.mcp_server")}

ACTION_TYPE_TERMINATE_IDLE_CONNECTION = "terminate_idle_connection"


async def propose_idle_connection_remediation(state: AgentState) -> dict:
    diagnosis = state["diagnosis"]
    existing_remediation = state["remediation"] or []

    if diagnosis["risk_tier"] == "low":
        return {"remediation": existing_remediation or None}

    environment = state["context"]["environment"]

    client = MultiServerMCPClient(MCP_SERVERS)
    tools = await client.get_tools()
    candidates_tool = next(t for t in tools if t.name == "get_idle_in_transaction_connections")
    idle_connections = parse_mcp_result(
        await invoke_tool(
            candidates_tool,
            {
                "environment": environment,
                "min_idle_seconds": settings.remediation_idle_connection_threshold_seconds,
            },
        )
    )

    # Duration alone isn't enough -- see the phase's own design note: terminating a
    # connection is more disruptive than cancelling a query, so only connections
    # confirmed to actually be blocking something (not just sitting idle) become
    # candidates at all. This data is already in state["context"]["lock_waits"] from
    # gather_context.py -- no new tool call needed for this half of the gate.
    blocking_pids = {lock["blocking_pid"] for lock in state["context"]["lock_waits"]}
    candidates = [c for c in idle_connections if c["pid"] in blocking_pids]

    if not candidates:
        return {"remediation": existing_remediation or None}

    llm = get_llm().with_structured_output(RemediationProposal)
    prompt = build_prompt(environment, diagnosis, state["investigation"], candidates)
    proposal: RemediationProposal = await llm.ainvoke(prompt)

    candidates_by_pid = {c["pid"]: c for c in candidates}
    incident_id = uuid.UUID(state["incident_id"])

    db = SessionLocal()
    try:
        created = []
        for decision in proposal.proposals:
            candidate = candidates_by_pid.get(decision.pid)
            if candidate is None:
                logger.warning(
                    "raw_event_id=%s propose_idle_connection_remediation: dropping hallucinated pid=%s",
                    state["raw_event"]["id"],
                    decision.pid,
                )
                continue
            if not decision.should_propose:
                continue
            action = create_remediation_action(
                db,
                incident_id=incident_id,
                action_type=ACTION_TYPE_TERMINATE_IDLE_CONNECTION,
                environment=environment,
                target_pid=decision.pid,
                target_query=candidate["query"],
                target_duration_seconds=candidate["duration_seconds"],
                target_backend_start=candidate["backend_start"],
                rationale=decision.rationale,
            )
            created.append(action_to_dict(action))

        if created:
            recompute_incident_status(db, incident_id)
    finally:
        db.close()

    logger.info(
        "raw_event_id=%s propose_idle_connection_remediation: idle=%d blocking=%d proposed=%d",
        state["raw_event"]["id"],
        len(idle_connections),
        len(candidates),
        len(created),
    )

    combined = existing_remediation + created
    return {"remediation": combined or None}
