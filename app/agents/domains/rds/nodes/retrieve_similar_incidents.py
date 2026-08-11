import logging

from app.agents.shared.state.agent import AgentState
from config.vectorstore import get_vectorstore

logger = logging.getLogger(__name__)


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

    logger.info("raw_event_id=%s found=%d", state["raw_event"]["id"], len(docs))

    return {"similar_incidents": [doc.metadata for doc in docs]}
