import json

from app.agents.shared.schema.diagnosis import Diagnosis
from app.agents.shared.state.agent import AgentState
from config.llm import get_llm


async def diagnose(state: AgentState) -> dict:
    llm = get_llm().with_structured_output(Diagnosis)
    prompt = (
        "You are diagnosing an AWS RDS/Aurora CloudWatch alarm for a DevOps monitoring system.\n\n"
        f"Alarm payload:\n{json.dumps(state['raw_event']['payload'], indent=2, default=str)}\n\n"
        f"Additional context gathered (cluster config + recent metric trend):\n"
        f"{json.dumps(state['context'], indent=2, default=str)}\n\n"
        "Produce a diagnosis: a short title, a description of what's happening and the likely "
        "cause, and a risk tier (low/medium/high)."
    )
    diagnosis: Diagnosis = await llm.ainvoke(prompt)
    return {"diagnosis": diagnosis.model_dump(mode="json")}
