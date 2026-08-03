from config.embeddings import get_embeddings


def embed_text(text: str) -> list[float]:
    return get_embeddings().embed_query(text)
