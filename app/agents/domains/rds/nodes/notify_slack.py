from app.agents.shared.state.agent import AgentState
from app.services.slack_notifier import post_diagnosis


async def notify_slack(state: AgentState) -> dict:
    await post_diagnosis(incident_id=state["incident_id"], diagnosis=state["diagnosis"])
    return {"slack_message_ts": None}
