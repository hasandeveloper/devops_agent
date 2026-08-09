import os

from config.settings import settings


def configure_tracing() -> None:
    """Bridge our own Settings into os.environ for LangSmith's tracer to see."""
    if not settings.langsmith_tracing:
        return

    # langchain-core's LangSmith tracer is a global callback that reads these
    # directly from os.environ -- unlike get_llm()/get_embeddings(), there's no
    # constructor to pass settings into, so os.environ is the only way in.
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_ENDPOINT"] = settings.langsmith_endpoint
    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
