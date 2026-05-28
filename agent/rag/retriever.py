from __future__ import annotations

"""Retriever facade for local RAG."""

from agent.rag.embedder import EmbeddingProvider
from agent.rag.models import RetrievalResult
from agent.rag.vector_store import SQLiteVectorStore


class Retriever:
    """Embed a query and retrieve matching chunks from the vector store."""

    def __init__(self, vector_store: SQLiteVectorStore, embedder: EmbeddingProvider) -> None:
        self.vector_store = vector_store
        self.embedder = embedder

    def retrieve(
        self,
        *,
        query: str,
        project: str = "",
        doc_type: str | list[str] | None = None,
        top_k: int = 3,
        min_score: float = 0.0,
    ) -> list[RetrievalResult]:
        doc_types = [doc_type] if isinstance(doc_type, str) else doc_type
        embedding = self.embedder.embed(query)
        return self.vector_store.search(query_embedding=embedding, project=project, doc_types=doc_types, top_k=top_k, min_score=min_score)
