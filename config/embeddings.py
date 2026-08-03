from langchain_openai import OpenAIEmbeddings

from config import settings


def get_embeddings() -> OpenAIEmbeddings:
    """The single place every domain agent gets its embeddings client from.

    Always OpenAI, independent of llm_provider -- Anthropic has no embeddings API.
    """
    return OpenAIEmbeddings(model=settings.embedding_model, api_key=settings.openai_api_key)
