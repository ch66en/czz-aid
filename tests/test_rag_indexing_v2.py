from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from agent.config import AppConfig
from agent.rag.embedder import DeterministicEmbeddingProvider
from agent.rag.knowledge_service import KnowledgeService
from agent.rag.local_doc_loader import LocalDocLoader
from agent.rag.models import KnowledgeChunk, KnowledgeDocument
from agent.rag.skill_chunker import SkillChunker
from agent.rag.skill_loader import SkillLoader
from agent.rag.vector_store import EmbeddingDimensionMismatchError, SQLiteVectorStore


class CountingEmbedder(DeterministicEmbeddingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.batch_calls = 0

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.batch_calls += 1
        return super().embed_batch(texts)


def _document(doc_id: str = "skill:demo") -> KnowledgeDocument:
    return KnowledgeDocument(
        doc_id=doc_id,
        source="skill",
        doc_type="skill",
        project="demo",
        title="Demo",
        content="# Demo\n\n## 适用场景\nNPE\n\n## 推荐排查步骤\nRead code\n\n## 验证方式\nmvn test",
        content_hash="sha256:doc",
        metadata={"skill_type": "review_passed", "use_types": ["recommended_fix", "validation_hint"], "project": "demo"},
    )


def test_legacy_skill_is_unclassified_and_never_recommended(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "old-skill"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text("# Old\n\nlegacy advice", encoding="utf-8")

    document = SkillLoader(tmp_path / "skills", default_project="demo").load()[0]

    assert document.metadata["skill_type"] == "legacy_unclassified"
    assert document.metadata["use_types"] == ["debug_hint"]
    assert "recommended_fix" not in document.metadata["use_types"]


def test_skill_chunker_preserves_source_metadata() -> None:
    document = _document()

    chunks = SkillChunker().chunk(document)

    assert {item.child_type for item in chunks} == {"signal_chunk", "fix_chunk", "validation_chunk"}
    assert all(item.metadata["skill_type"] == "review_passed" for item in chunks)
    assert all(item.doc_id == document.doc_id for item in chunks)


def test_local_doc_without_authority_is_unclassified(tmp_path: Path) -> None:
    path = tmp_path / "docs" / "api" / "order.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\nproject: demo\n---\n# API\n\nRule", encoding="utf-8")

    document = LocalDocLoader(tmp_path / "docs", default_project="demo").load()[0]

    assert document.metadata["authority"] == "unclassified"


def test_rag_schema_migrates_existing_database(tmp_path: Path) -> None:
    db_path = tmp_path / "rag.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE rag_documents (doc_id TEXT PRIMARY KEY, source TEXT, doc_type TEXT, project TEXT, module TEXT, title TEXT, uri TEXT, content_hash TEXT, updated_at TEXT, metadata_json TEXT)"
        )
        conn.execute(
            "CREATE TABLE rag_chunks (chunk_id TEXT PRIMARY KEY, doc_id TEXT, source TEXT, doc_type TEXT, project TEXT, module TEXT, title TEXT, heading_path_json TEXT, content TEXT, content_hash TEXT, embedding_json TEXT, metadata_json TEXT, updated_at TEXT)"
        )

    status = SQLiteVectorStore(str(db_path)).index_status()
    with sqlite3.connect(db_path) as conn:
        document_columns = {row[1] for row in conn.execute("PRAGMA table_info(rag_documents)")}
        chunk_columns = {row[1] for row in conn.execute("PRAGMA table_info(rag_chunks)")}

    assert "content" in document_columns
    assert {"child_type", "section_name"}.issubset(chunk_columns)
    assert status["meta"]["schema_version"] == "2"


def test_upsert_updates_parent_vector_and_fts_in_one_transaction(tmp_path: Path) -> None:
    store = SQLiteVectorStore(str(tmp_path / "rag.db"))
    embedder = DeterministicEmbeddingProvider()
    document = _document()
    chunk = SkillChunker().chunk(document)[0]
    chunk.embedding = embedder.embed(chunk.content)

    store.upsert_document(document, [chunk])

    assert store.get_document(document.doc_id).content == document.content  # type: ignore[union-attr]
    assert store.search(query_embedding=embedder.embed(chunk.content), project="demo", doc_types=["skill"])
    assert store.search_bm25(query="NPE", project="demo", doc_types=["skill"])
    assert store.index_status()["chunks"] == store.index_status()["fts_chunks"] == 1


def test_unchanged_document_skips_embedding_and_deleted_source_is_pruned(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "skill-demo"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text("# Demo\n\n## 适用场景\nNPE", encoding="utf-8")
    skill_dir.joinpath("skill.meta.json").write_text(
        json.dumps({"name": "skill-demo", "schema_version": 2, "skill_type": "review_passed", "use_types": ["recommended_fix"], "project": "demo"}),
        encoding="utf-8",
    )
    config = AppConfig(workspace=str(tmp_path), project={"name": "demo"}, rag={"db_path": str(tmp_path / "rag.db")})
    embedder = CountingEmbedder()
    service = KnowledgeService(config=config, embedder=embedder)

    assert service.index_skills() == 1
    assert service.index_skills() == 0
    assert embedder.batch_calls == 1

    skill_dir.joinpath("SKILL.md").unlink()
    skill_dir.joinpath("skill.meta.json").unlink()
    skill_dir.rmdir()
    assert service.index_skills() == 0
    assert service.vector_store.list_document_ids(source="skill") == set()


def test_embedding_dimension_mismatch_requires_rebuild(tmp_path: Path) -> None:
    store = SQLiteVectorStore(str(tmp_path / "rag.db"))
    document = _document()
    chunk = KnowledgeChunk(
        chunk_id="chunk-1",
        doc_id=document.doc_id,
        source="skill",
        doc_type="skill",
        project="demo",
        title="Demo",
        content="NPE",
        content_hash="chunk",
        embedding=[1.0, 0.0],
    )
    store.upsert_document(document, [chunk])

    with pytest.raises(EmbeddingDimensionMismatchError):
        store.search(query_embedding=[1.0, 0.0, 0.0], project="demo", doc_types=["skill"])


def test_rag_rebuild_recreates_fts_and_vectors(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "skill-demo"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text("# Demo\n\n## 适用场景\nNPE", encoding="utf-8")
    skill_dir.joinpath("skill.meta.json").write_text(
        json.dumps({"name": "skill-demo", "schema_version": 2, "skill_type": "review_passed", "use_types": ["recommended_fix"], "project": "demo"}),
        encoding="utf-8",
    )
    config = AppConfig(workspace=str(tmp_path), project={"name": "demo"}, rag={"db_path": str(tmp_path / "rag.db")})
    service = KnowledgeService(config=config)

    summary = service.rebuild_index()
    status = service.index_status()

    assert summary["skills"] == 1
    assert status["documents"] == 1
    assert status["chunks"] == status["fts_chunks"] > 0
