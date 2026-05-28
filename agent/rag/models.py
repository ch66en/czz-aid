from __future__ import annotations

"""Data models for local RAG indexing and retrieval."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class KnowledgeDocument(BaseModel):
    doc_id: str
    source: str
    doc_type: str
    project: str
    module: str = ""
    title: str
    content: str
    uri: str = ""
    updated_at: str = ""
    content_hash: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeChunk(BaseModel):
    chunk_id: str
    doc_id: str
    source: str
    doc_type: str
    project: str
    module: str = ""
    title: str
    heading_path: list[str] = Field(default_factory=list)
    content: str
    token_count: int = 0
    content_hash: str
    embedding: list[float] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class RetrievalResult(BaseModel):
    chunk_id: str
    doc_id: str
    source: str
    doc_type: str
    project: str
    module: str = ""
    title: str
    content: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class RagContext(BaseModel):
    skills: list[RetrievalResult] = Field(default_factory=list)
    project_docs: list[RetrievalResult] = Field(default_factory=list)
