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
    child_type: str = ""
    section_name: str = ""
    content: str
    token_count: int = 0
    content_hash: str
    embedding: list[float] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class RetrievalResult(BaseModel):
    chunk_id: str
    doc_id: str
    parent_id: str = ""
    source: str
    doc_type: str
    project: str
    module: str = ""
    title: str
    content: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)
    retrieval_sources: list[str] = Field(default_factory=list)
    ranks: dict[str, int] = Field(default_factory=dict)


class RagContext(BaseModel):
    skills: list[RetrievalResult] = Field(default_factory=list)
    project_docs: list[RetrievalResult] = Field(default_factory=list)


class RepairRagQuery(BaseModel):
    project: str
    module_candidates: list[str] = Field(default_factory=list)
    exception_type: str = ""
    message: str = ""
    class_name: str = ""
    method_name: str = ""
    package_name: str = ""
    top_business_frame: str = ""
    request_path: str = ""
    repair_stage: str = "before_edit"
    root_cause_hint: str = ""
    skill_bm25_query: str = ""
    skill_vector_query: str = ""
    project_doc_bm25_query: str = ""
    project_doc_vector_query: str = ""


class KnowledgeReference(BaseModel):
    source: str
    source_type: str
    doc_id: str
    chunk_id: str = ""
    parent_id: str = ""
    title: str = ""
    uri: str = ""


class RagContextItem(BaseModel):
    text: str
    source: KnowledgeReference
    confidence: str = "medium"
    use_type: str = ""


class RagRepairContext(BaseModel):
    query_summary: str = ""
    hard_constraints: list[RagContextItem] = Field(default_factory=list)
    soft_hints: list[RagContextItem] = Field(default_factory=list)
    avoid_patterns: list[RagContextItem] = Field(default_factory=list)
    validation_hints: list[RagContextItem] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    selected_sources: list[KnowledgeReference] = Field(default_factory=list)
    confidence: str = "low"
    missing_info: list[str] = Field(default_factory=list)


class RagStatus(BaseModel):
    status: str = "success"
    degraded_stages: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    fallback_strategies: list[str] = Field(default_factory=list)
    candidate_count: int = 0
    selected_source_count: int = 0
    synthesizer_latency_ms: int = 0
