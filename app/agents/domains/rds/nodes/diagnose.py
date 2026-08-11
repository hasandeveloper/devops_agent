import logging

from app.agents.shared.schema.diagnosis import Diagnosis
from app.agents.shared.state.agent import AgentState
from app.prompts.rds.diagnose import build_prompt
from config.llm import get_llm

logger = logging.getLogger(__name__)


async def diagnose(state: AgentState) -> dict:
    llm = get_llm().with_structured_output(Diagnosis)
    prompt = build_prompt(
        state["raw_event"]["payload"], state["context"], state["similar_incidents"], state["investigation"]
    )
    diagnosis: Diagnosis = await llm.ainvoke(prompt)

    logger.info(
        "raw_event_id=%s risk_tier=%s title=%r",
        state["raw_event"]["id"],
        diagnosis.risk_tier.value,
        diagnosis.title,
    )

    return {"diagnosis": diagnosis.model_dump(mode="json")}
