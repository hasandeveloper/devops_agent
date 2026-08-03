from app.agents.shared.state.agent import AgentState
from config.vectorstore import get_vectorstore


def _build_query_text(payload: dict) -> str:
    trigger = payload.get("Trigger", {})
    dims = ", ".join(f"{d.get('name')}={d.get('value')}" for d in trigger.get("Dimensions", []))
    return (
        f"{payload.get('AlarmName', '')} {trigger.get('Namespace', '')} "
        f"{trigger.get('MetricName', '')} {dims} {payload.get('NewStateReason', '')}"
    )


def retrieve_similar_incidents(state: AgentState) -> dict:
    query_text = _build_query_text(state["raw_event"]["payload"])

    retriever = get_vectorstore().as_retriever(
        search_type="mmr",
        search_kwargs={"k": 3, "fetch_k": 20, "lambda_mult": 0.7},
    )
    docs = retriever.invoke(query_text)

    return {"similar_incidents": [doc.metadata for doc in docs]}
