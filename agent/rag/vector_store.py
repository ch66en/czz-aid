from __future__ import annotations

"""SQLite-backed vector and FTS5 store for local RAG."""

from datetime import datetime
import json
import math
import re
from typing import Any

from agent.rag.migrations import ensure_rag_schema, get_index_meta, rebuild_fts, set_index_meta
from agent.rag.models import KnowledgeChunk, KnowledgeDocument, RetrievalResult
from agent.storage.sqlite_repo import SQLiteRepo


class EmbeddingDimensionMismatchError(RuntimeError):
    """Raised when an embedding query does not match the persisted index."""


class IndexRebuildRequiredError(RuntimeError):
    """Raised when the configured embedding identity changed."""


class SQLiteVectorStore:
    """Persist parent documents, child chunks, vectors, and FTS rows."""

    def __init__(self, db_path: str) -> None:
        self.repo = SQLiteRepo(db_path)

    def upsert_document(self, document: KnowledgeDocument, chunks: list[KnowledgeChunk]) -> None:
        self._ensure_schema()
        dimension = self._embedding_dimension(chunks)
        with self.repo.connect() as conn:
            ensure_rag_schema(conn)
            self._validate_or_record_dimension(conn, dimension)
            conn.execute(
                """
                INSERT INTO rag_documents (
                    doc_id, source, doc_type, project, module, title, uri, content,
                    content_hash, updated_at, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(doc_id) DO UPDATE SET
                    source = excluded.source,
                    doc_type = excluded.doc_type,
                    project = excluded.project,
                    module = excluded.module,
                    title = excluded.title,
                    uri = excluded.uri,
                    content = excluded.content,
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
                    document.content,
                    document.content_hash,
                    document.updated_at,
                    json.dumps(document.metadata, ensure_ascii=False, default=str),
                ),
            )
            conn.execute("DELETE FROM rag_chunks_fts WHERE doc_id = ?", (document.doc_id,))
            conn.execute("DELETE FROM rag_chunks WHERE doc_id = ?", (document.doc_id,))
            for chunk in chunks:
                conn.execute(
                    """
                    INSERT INTO rag_chunks (
                        chunk_id, doc_id, source, doc_type, project, module, title, heading_path_json,
                        child_type, section_name, content, content_hash, embedding_json, metadata_json, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        chunk.child_type,
                        chunk.section_name,
                        chunk.content,
                        chunk.content_hash,
                        json.dumps(chunk.embedding),
                        json.dumps(chunk.metadata, ensure_ascii=False, default=str),
                        chunk.updated_at,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO rag_chunks_fts (
                        chunk_id, doc_id, project, doc_type, module, title, heading_path, content
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk.chunk_id,
                        chunk.doc_id,
                        chunk.project,
                        chunk.doc_type,
                        chunk.module,
                        chunk.title,
                        " / ".join(chunk.heading_path),
                        chunk.content,
                    ),
                )

    def set_index_identity(self, *, embedding_provider: str, embedding_model: str, embedding_dimension: int = 0) -> None:
        self._ensure_schema()
        with self.repo.connect() as conn:
            ensure_rag_schema(conn)
            count = int(conn.execute("SELECT COUNT(*) FROM rag_chunks").fetchone()[0])
            for key, value in {
                "embedding_provider": embedding_provider,
                "embedding_model": embedding_model,
            }.items():
                previous = get_index_meta(conn, key)
                if count and previous and value and previous != value:
                    raise IndexRebuildRequiredError(f"{key} changed; run rag-rebuild-index")
                if value:
                    set_index_meta(conn, key, value)
            self._validate_or_record_dimension(conn, embedding_dimension)

    def get_document_content_hash(self, doc_id: str) -> str:
        self._ensure_schema()
        with self.repo.connect() as conn:
            row = conn.execute("SELECT content_hash FROM rag_documents WHERE doc_id = ?", (doc_id,)).fetchone()
        return str(row[0]) if row else ""

    def get_document(self, doc_id: str) -> KnowledgeDocument | None:
        self._ensure_schema()
        with self.repo.connect() as conn:
            row = conn.execute(
                """
                SELECT doc_id, source, doc_type, project, module, title, uri, content, updated_at, content_hash, metadata_json
                FROM rag_documents WHERE doc_id = ?
                """,
                (doc_id,),
            ).fetchone()
        if not row:
            return None
        return KnowledgeDocument(
            doc_id=row[0],
            source=row[1],
            doc_type=row[2],
            project=row[3],
            module=row[4] or "",
            title=row[5],
            uri=row[6] or "",
            content=row[7] or "",
            updated_at=row[8] or "",
            content_hash=row[9],
            metadata=self._parse_json(row[10]),
        )

    def list_document_ids(self, *, source: str = "") -> set[str]:
        self._ensure_schema()
        with self.repo.connect() as conn:
            if source:
                rows = conn.execute("SELECT doc_id FROM rag_documents WHERE source = ?", (source,)).fetchall()
            else:
                rows = conn.execute("SELECT doc_id FROM rag_documents").fetchall()
        return {str(row[0]) for row in rows}

    def delete_documents_not_in(self, *, source: str, active_doc_ids: set[str]) -> int:
        existing = self.list_document_ids(source=source)
        stale = sorted(existing - active_doc_ids)
        if not stale:
            return 0
        self._delete_documents(stale)
        return len(stale)

    def clear(self) -> None:
        self._ensure_schema()
        with self.repo.connect() as conn:
            conn.execute("DELETE FROM rag_chunks_fts")
            conn.execute("DELETE FROM rag_chunks")
            conn.execute("DELETE FROM rag_documents")
            conn.execute("DELETE FROM rag_index_meta WHERE key IN ('embedding_provider', 'embedding_model', 'embedding_dimension')")
            set_index_meta(conn, "last_full_rebuild_at", datetime.utcnow().isoformat())

    def rebuild_fts(self) -> None:
        self._ensure_schema()
        with self.repo.connect() as conn:
            rebuild_fts(conn)
            set_index_meta(conn, "last_full_rebuild_at", datetime.utcnow().isoformat())

    def index_status(self) -> dict[str, Any]:
        self._ensure_schema()
        with self.repo.connect() as conn:
            documents = int(conn.execute("SELECT COUNT(*) FROM rag_documents").fetchone()[0])
            chunks = int(conn.execute("SELECT COUNT(*) FROM rag_chunks").fetchone()[0])
            fts_chunks = int(conn.execute("SELECT COUNT(*) FROM rag_chunks_fts").fetchone()[0])
            meta = {str(row[0]): str(row[1]) for row in conn.execute("SELECT key, value FROM rag_index_meta").fetchall()}
        return {"documents": documents, "chunks": chunks, "fts_chunks": fts_chunks, "meta": meta}

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
        self._validate_query_dimension(query_embedding)
        clauses, params = self._filters(project, doc_types)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.repo.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT chunk_id, doc_id, source, doc_type, project, module, title, content,
                       embedding_json, metadata_json, child_type, section_name
                FROM rag_chunks
                {where}
                """,
                params,
            ).fetchall()

        results: list[RetrievalResult] = []
        for row in rows:
            embedding = self._parse_embedding(row[8])
            if len(embedding) != len(query_embedding):
                raise EmbeddingDimensionMismatchError(f"stored chunk embedding dimension mismatch: {row[0]}")
            score = self._cosine(query_embedding, embedding)
            if score < min_score:
                continue
            results.append(self._retrieval_result(row, score=score, metadata_index=9, child_type_index=10, section_index=11, retrieval_source="vector"))
        return sorted(results, key=lambda item: item.score, reverse=True)[:top_k]

    def search_bm25(
        self,
        *,
        query: str,
        project: str = "",
        doc_types: list[str] | None = None,
        top_k: int = 20,
    ) -> list[RetrievalResult]:
        self._ensure_schema()
        fts_query = self._fts_query(query)
        if not fts_query:
            return []
        clauses = ["rag_chunks_fts MATCH ?"]
        params: list[str] = [fts_query]
        if project:
            clauses.append("f.project = ?")
            params.append(project)
        if doc_types:
            placeholders = ", ".join("?" for _ in doc_types)
            clauses.append(f"f.doc_type IN ({placeholders})")
            params.extend(doc_types)
        with self.repo.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT c.chunk_id, c.doc_id, c.source, c.doc_type, c.project, c.module, c.title, c.content,
                       c.metadata_json, c.child_type, c.section_name, -bm25(rag_chunks_fts) AS score
                FROM rag_chunks_fts AS f
                JOIN rag_chunks AS c ON c.chunk_id = f.chunk_id
                WHERE {' AND '.join(clauses)}
                ORDER BY score DESC
                LIMIT ?
                """,
                [*params, top_k],
            ).fetchall()
        return [
            self._retrieval_result(row, score=float(row[11]), metadata_index=8, child_type_index=9, section_index=10, retrieval_source="bm25")
            for row in rows
        ]

    def _delete_documents(self, doc_ids: list[str]) -> None:
        placeholders = ", ".join("?" for _ in doc_ids)
        with self.repo.connect() as conn:
            conn.execute(f"DELETE FROM rag_chunks_fts WHERE doc_id IN ({placeholders})", doc_ids)
            conn.execute(f"DELETE FROM rag_chunks WHERE doc_id IN ({placeholders})", doc_ids)
            conn.execute(f"DELETE FROM rag_documents WHERE doc_id IN ({placeholders})", doc_ids)

    def _ensure_schema(self) -> None:
        with self.repo.connect() as conn:
            ensure_rag_schema(conn)

    def _filters(self, project: str, doc_types: list[str] | None) -> tuple[list[str], list[str]]:
        clauses: list[str] = []
        params: list[str] = []
        if project:
            clauses.append("project = ?")
            params.append(project)
        if doc_types:
            placeholders = ", ".join("?" for _ in doc_types)
            clauses.append(f"doc_type IN ({placeholders})")
            params.extend(doc_types)
        return clauses, params

    def _retrieval_result(
        self,
        row: Any,
        *,
        score: float,
        metadata_index: int,
        child_type_index: int,
        section_index: int,
        retrieval_source: str,
    ) -> RetrievalResult:
        metadata = self._parse_json(row[metadata_index])
        metadata.update({"child_type": row[child_type_index] or "", "section_name": row[section_index] or ""})
        return RetrievalResult(
            chunk_id=row[0],
            doc_id=row[1],
            parent_id=row[1] if row[3] == "skill" else "",
            source=row[2],
            doc_type=row[3],
            project=row[4],
            module=row[5] or "",
            title=row[6],
            content=row[7],
            score=score,
            metadata=metadata,
            retrieval_sources=[retrieval_source],
        )

    def _embedding_dimension(self, chunks: list[KnowledgeChunk]) -> int:
        dimensions = {len(chunk.embedding) for chunk in chunks if chunk.embedding}
        if len(dimensions) > 1:
            raise EmbeddingDimensionMismatchError("document contains mixed embedding dimensions")
        return next(iter(dimensions), 0)

    def _validate_or_record_dimension(self, conn: Any, dimension: int) -> None:
        if not dimension:
            return
        previous = get_index_meta(conn, "embedding_dimension")
        if previous and int(previous) != dimension:
            raise EmbeddingDimensionMismatchError("embedding dimension changed; run rag-rebuild-index")
        set_index_meta(conn, "embedding_dimension", str(dimension))

    def _validate_query_dimension(self, query_embedding: list[float]) -> None:
        if not query_embedding:
            return
        with self.repo.connect() as conn:
            ensure_rag_schema(conn)
            previous = get_index_meta(conn, "embedding_dimension")
        if previous and int(previous) != len(query_embedding):
            raise EmbeddingDimensionMismatchError("query embedding dimension does not match index")

    def _parse_embedding(self, raw: str) -> list[float]:
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return []
        return [float(item) for item in value] if isinstance(value, list) else []

    def _parse_json(self, raw: str) -> dict[str, Any]:
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _fts_query(self, query: str) -> str:
        terms = re.findall(r"[A-Za-z0-9_.$/:-]+|[\u4e00-\u9fff]+", query)
        unique = list(dict.fromkeys(term for term in terms if term.strip()))
        return " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in unique[:32])

    def _cosine(self, left: list[float], right: list[float]) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        dot = sum(a * b for a, b in zip(left, right))
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return dot / (left_norm * right_norm)
