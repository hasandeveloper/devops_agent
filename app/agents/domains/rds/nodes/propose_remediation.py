import logging
import uuid

from langchain_mcp_adapters.client import MultiServerMCPClient

from app.agents.shared.schema.remediation import RemediationProposal
from app.agents.shared.state.agent import AgentState
from app.prompts.rds.remediation import build_prompt
from app.services.remediation_service import action_to_dict, create_remediation_action, recompute_incident_status
from config.llm import get_llm
from config.mcp import parse_mcp_list_result, stdio_server
from config.reliability.mcp_timeouts import invoke_tool
from config.settings import settings
from db import SessionLocal

logger = logging.getLogger(__name__)

MCP_SERVERS = {"rds": stdio_server("app.agents.tools.mcp.rds.mcp_server")}

ACTION_TYPE_CANCEL_QUERY = "cancel_query"


async def propose_remediation(state: AgentState) -> dict:
    diagnosis = state["diagnosis"]
    if diagnosis["risk_tier"] == "low":
        # No point proposing action on an informational alarm -- see propose_remediation's
        # scope note in the HITL plan: this gate exists per-action-type, not globally.
        return {"remediation": None}

    if state["raw_event"]["payload"].get("NewStateValue") != "ALARM":
        # CloudWatch still sends a notification when an alarm clears (NewStateValue
        # "OK") -- by the time that notification reaches this pipeline, whatever
        # triggered it may already be gone. Proposing to kill something because of an
        # alarm that's already resolved is backwards, and a real incident seen live:
        # a second, unrelated alarm's "Resolved" pipeline run re-proposed pids that had
        # just been terminated moments earlier by a *different* alarm's remediation.
        logger.info(
            "raw_event_id=%s propose_remediation: skipping, alarm state=%s (not ALARM)",
            state["raw_event"]["id"],
            state["raw_event"]["payload"].get("NewStateValue"),
        )
        return {"remediation": None}

    environment = state["context"]["environment"]

    client = MultiServerMCPClient(MCP_SERVERS)
    tools = await client.get_tools()
    candidates_tool = next(t for t in tools if t.name == "get_long_running_queries")
    # parse_mcp_list_result, not parse_mcp_result -- exactly one long-running query is a
    # completely ordinary result, not a rare edge case, and parse_mcp_result alone would
    # collapse it to a bare dict instead of a one-element list (see its docstring).
    candidates = parse_mcp_list_result(
        await invoke_tool(
            candidates_tool,
            {"environment": environment, "min_duration_seconds": settings.remediation_long_query_threshold_seconds},
        )
    )

    if not candidates:
        return {"remediation": None}

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
                # The LLM referenced a pid that was never offered -- same belt-and-suspenders
                # spirit as mcp_server.py's _explain_safety_violation. Never trust a target
                # blindly, even for a read-only proposal.
                logger.warning(
                    "raw_event_id=%s propose_remediation: dropping hallucinated pid=%s",
                    state["raw_event"]["id"],
                    decision.pid,
                )
                continue
            if not decision.should_propose:
                continue
            action = create_remediation_action(
                db,
                incident_id=incident_id,
                action_type=ACTION_TYPE_CANCEL_QUERY,
                environment=environment,
                target_pid=decision.pid,
                target_query=candidate["query"],
                target_duration_seconds=candidate["duration_seconds"],
                rationale=decision.rationale,
            )
            created.append(action_to_dict(action))

        if created:
            recompute_incident_status(db, incident_id)
    finally:
        db.close()

    logger.info(
        "raw_event_id=%s propose_remediation: candidates=%d proposed=%d",
        state["raw_event"]["id"],
        len(candidates),
        len(created),
    )

    return {"remediation": created or None}
