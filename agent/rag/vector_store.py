from __future__ import annotations

"""SQLite-backed vector store for local RAG."""

import json
import math

from agent.rag.models import KnowledgeChunk, KnowledgeDocument, RetrievalResult
from agent.storage.sqlite_repo import SQLiteRepo


class SQLiteVectorStore:
    """Persist RAG documents/chunks and search by cosine similarity."""

    def __init__(self, db_path: str) -> None:
        self.repo = SQLiteRepo(db_path)

    def upsert_document(self, document: KnowledgeDocument, chunks: list[KnowledgeChunk]) -> None:
        self._ensure_schema()
        with self.repo.connect() as conn:
            conn.execute(
                """
                INSERT INTO rag_documents (doc_id, source, doc_type, project, module, title, uri, content_hash, updated_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(doc_id) DO UPDATE SET
                    source = excluded.source,
                    doc_type = excluded.doc_type,
                    project = excluded.project,
                    module = excluded.module,
                    title = excluded.title,
                    uri = excluded.uri,
                    content_hash = excluded.content_hash,
                    updated_at = excluded.updated_at,
                    metadata_json = excluded.metadata_json
                """,
                (
                    document.doc_id,
                    document.source,
                    document.doc_type,
                    document.project,
                    document.module,
                    document.title,
                    document.uri,
                    document.content_hash,
                    document.updated_at,
                    json.dumps(document.metadata, ensure_ascii=False, default=str),
                ),
            )
            conn.execute("DELETE FROM rag_chunks WHERE doc_id = ?", (document.doc_id,))
            for chunk in chunks:
                conn.execute(
                    """
                    INSERT INTO rag_chunks (
                        chunk_id, doc_id, source, doc_type, project, module, title, heading_path_json,
                        content, content_hash, embedding_json, metadata_json, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk.chunk_id,
                        chunk.doc_id,
                        chunk.source,
                        chunk.doc_type,
                        chunk.project,
                        chunk.module,
                        chunk.title,
                        json.dumps(chunk.heading_path, ensure_ascii=False),
                        chunk.content,
                        chunk.content_hash,
                        json.dumps(chunk.embedding),
                        json.dumps(chunk.metadata, ensure_ascii=False, default=str),
                        chunk.updated_at,
                    ),
                )

    def get_document_content_hash(self, doc_id: str) -> str:
        self._ensure_schema()
        with self.repo.connect() as conn:
            cursor = conn.execute("SELECT content_hash FROM rag_documents WHERE doc_id = ?", (doc_id,))
            row = cursor.fetchone()
        return str(row[0]) if row else ""

    def search(
        self,
        *,
        query_embedding: list[float],
        project: str = "",
        doc_types: list[str] | None = None,
        top_k: int = 3,
        min_score: float = 0.0,
    ) -> list[RetrievalResult]:
        self._ensure_schema()
        clauses: list[str] = []
        params: list[str] = []
        if project:
            clauses.append("project = ?")
            params.append(project)
        if doc_types:
            placeholders = ", ".join("?" for _ in doc_types)
            clauses.append(f"doc_type IN ({placeholders})")
            params.extend(doc_types)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = []
        with self.repo.connect() as conn:
            cursor = conn.execute(
                f"""
                SELECT chunk_id, doc_id, source, doc_type, project, module, title, content, embedding_json, metadata_json
                FROM rag_chunks
                {where}
                """,
                params,
            )
            rows = cursor.fetchall()

        results: list[RetrievalResult] = []
        for row in rows:
            embedding = self._parse_embedding(row[8])
            score = self._cosine(query_embedding, embedding)
            if score < min_score:
                continue
            metadata = self._parse_json(row[9])
            results.append(
                RetrievalResult(
                    chunk_id=row[0],
                    doc_id=row[1],
                    source=row[2],
                    doc_type=row[3],
                    project=row[4],
                    module=row[5] or "",
                    title=row[6],
                    content=row[7],
                    score=score,
                    metadata=metadata,
                )
            )
        return sorted(results, key=lambda item: item.score, reverse=True)[:top_k]

    def _ensure_schema(self) -> None:
        with self.repo.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rag_documents (
                    doc_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    doc_type TEXT NOT NULL,
                    project TEXT NOT NULL,
                    module TEXT,
                    title TEXT NOT NULL,
                    uri TEXT,
                    content_hash TEXT NOT NULL,
                    updated_at TEXT,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rag_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    doc_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    doc_type TEXT NOT NULL,
                    project TEXT NOT NULL,
                    module TEXT,
                    title TEXT NOT NULL,
                    heading_path_json TEXT NOT NULL,
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    embedding_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    updated_at TEXT,
                    FOREIGN KEY(doc_id) REFERENCES rag_documents(doc_id)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rag_chunks_project_doc_type ON rag_chunks(project, doc_type)")

    def _parse_embedding(self, raw: str) -> list[float]:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(value, list):
            return []
        return [float(item) for item in value]

    def _parse_json(self, raw: str) -> dict[str, object]:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def _cosine(self, left: list[float], right: list[float]) -> float:
        if not left or not right:
            return 0.0
        length = min(len(left), len(right))
        dot = sum(left[index] * right[index] for index in range(length))
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return dot / (left_norm * right_norm)
