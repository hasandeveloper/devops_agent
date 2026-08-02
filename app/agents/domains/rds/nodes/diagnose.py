from app.agents.shared.schema.diagnosis import Diagnosis
from app.agents.shared.state.agent import AgentState
from app.prompts.rds.diagnose import build_prompt
from config.llm import get_llm


async def diagnose(state: AgentState) -> dict:
    llm = get_llm().with_structured_output(Diagnosis)
    prompt = build_prompt(state["raw_event"]["payload"], state["context"])
    diagnosis: Diagnosis = await llm.ainvoke(prompt)
    return {"diagnosis": diagnosis.model_dump(mode="json")}
