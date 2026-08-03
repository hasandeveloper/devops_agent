from langchain_postgres import PGVector

from app.models.incidents import EMBEDDING_DIM
from config.embeddings import get_embeddings
from db import engine

_vectorstore: PGVector | None = None


def get_vectorstore() -> PGVector:
    """The single place every domain agent gets its incident vectorstore from.

    Lazily built once and reused -- unlike get_llm()/get_embeddings(), constructing
    this issues real DDL (CREATE TABLE IF NOT EXISTS), so it isn't cheap per-call.
    """
    global _vectorstore
    if _vectorstore is None:
        _vectorstore = PGVector(
            embeddings=get_embeddings(),
            connection=engine,
            collection_name="incidents",
            embedding_length=EMBEDDING_DIM,
        )
    return _vectorstore
