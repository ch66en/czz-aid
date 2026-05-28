import json
from pathlib import Path

from agent.config import AppConfig
from agent.models import BugEvent, StackFrame
from agent.rag.chunker import MarkdownChunker
from agent.rag.embedder import DeterministicEmbeddingProvider
from agent.rag.knowledge_service import KnowledgeService
from agent.rag.local_doc_loader import LocalDocLoader
from agent.rag.models import KnowledgeChunk, KnowledgeDocument
from agent.rag.retriever import Retriever
from agent.rag.skill_loader import SkillLoader
from agent.rag.vector_store import SQLiteVectorStore


def test_skill_loader_loads_skill_md(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "skill-demo-npe"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text("# Demo NPE\n\nUse Optional guard.", encoding="utf-8")
    skill_dir.joinpath("skill.meta.json").write_text(
        json.dumps({"name": "skill-demo-npe", "project": "demo", "module": "order", "description": "NPE skill"}),
        encoding="utf-8",
    )
    loader = SkillLoader(tmp_path / "skills", default_project="fallback")

    documents = loader.load()

    assert len(documents) == 1
    assert documents[0].doc_id == "skill:skill-demo-npe"
    assert documents[0].doc_type == "skill"
    assert documents[0].project == "demo"
    assert documents[0].module == "order"
    assert documents[0].title == "NPE skill"
    assert "Optional guard" in documents[0].content


def test_vector_store_upsert_and_search(tmp_path: Path) -> None:
    db_path = tmp_path / "rag.db"
    store = SQLiteVectorStore(str(db_path))
    embedder = DeterministicEmbeddingProvider()
    document = KnowledgeDocument(
        doc_id="skill:order-npe",
        source="skill",
        doc_type="skill",
        project="demo",
        title="Order NPE",
        content="NullPointerException OrderService optional guard",
        content_hash="sha256:doc",
    )
    chunk = KnowledgeChunk(
        chunk_id="chunk-1",
        doc_id=document.doc_id,
        source=document.source,
        doc_type=document.doc_type,
        project=document.project,
        title=document.title,
        content=document.content,
        content_hash="sha256:chunk",
        embedding=embedder.embed(document.content),
    )

    store.upsert_document(document, [chunk])
    results = store.search(query_embedding=embedder.embed("NullPointerException OrderService"), project="demo", doc_types=["skill"], top_k=3, min_score=0.0)

    assert len(results) == 1
    assert results[0].doc_id == "skill:order-npe"
    assert results[0].score > 0


def test_retriever_filters_by_project(tmp_path: Path) -> None:
    db_path = tmp_path / "rag.db"
    store = SQLiteVectorStore(str(db_path))
    embedder = DeterministicEmbeddingProvider()
    for project in ["order-service", "payment-service"]:
        document = KnowledgeDocument(
            doc_id=f"skill:{project}",
            source="skill",
            doc_type="skill",
            project=project,
            title=project,
            content="IllegalArgumentException validation handler",
            content_hash=f"sha256:{project}",
        )
        chunk = KnowledgeChunk(
            chunk_id=f"chunk:{project}",
            doc_id=document.doc_id,
            source="skill",
            doc_type="skill",
            project=project,
            title=project,
            content=document.content,
            content_hash=f"sha256:chunk:{project}",
            embedding=embedder.embed(document.content),
        )
        store.upsert_document(document, [chunk])
    retriever = Retriever(store, embedder)

    results = retriever.retrieve(query="validation handler", project="order-service", doc_type="skill", top_k=5, min_score=0.0)

    assert [item.project for item in results] == ["order-service"]


def test_chunker_preserves_heading_path() -> None:
    document = KnowledgeDocument(
        doc_id="skill:demo",
        source="skill",
        doc_type="skill",
        project="demo",
        title="Demo",
        content="# Root\n\nIntro\n\n## Fix\n\nUse guard",
        content_hash="sha256:doc",
    )
    chunks = MarkdownChunker().chunk(document)

    assert chunks[0].heading_path == ["Root"]
    assert chunks[1].heading_path == ["Root", "Fix"]


def test_local_doc_loader_parses_markdown_front_matter(tmp_path: Path) -> None:
    doc_path = tmp_path / "docs" / "api" / "order.md"
    doc_path.parent.mkdir(parents=True)
    doc_path.write_text(
        "---\n"
        "project: mall-service\n"
        "module: order\n"
        "tags: [order, payment]\n"
        "---\n"
        "# Create Order API\n\n"
        "Amount must be positive.",
        encoding="utf-8",
    )
    loader = LocalDocLoader(tmp_path / "docs", default_project="demo-service")

    documents = loader.load()

    assert len(documents) == 1
    assert documents[0].doc_type == "api_doc"
    assert documents[0].project == "mall-service"
    assert documents[0].module == "order"
    assert documents[0].metadata["tags"] == ["order", "payment"]
    assert documents[0].title == "Create Order API"
    assert "project:" not in documents[0].content


def test_local_doc_loader_defaults_metadata_without_front_matter(tmp_path: Path) -> None:
    doc_path = tmp_path / "docs" / "db" / "schema.txt"
    doc_path.parent.mkdir(parents=True)
    doc_path.write_text("orders table stores item ids", encoding="utf-8")
    loader = LocalDocLoader(tmp_path / "docs", default_project="demo-service")

    documents = loader.load()

    assert len(documents) == 1
    assert documents[0].doc_type == "db_doc"
    assert documents[0].project == "demo-service"
    assert documents[0].module == ""
    assert documents[0].title == "schema"


def test_knowledge_service_indexes_local_docs(tmp_path: Path) -> None:
    doc_path = tmp_path / "docs" / "design" / "order-flow.md"
    doc_path.parent.mkdir(parents=True)
    doc_path.write_text(
        "---\nproject: mall-service\nmodule: order\n---\n"
        "# Order Flow\n\n"
        "Order status transitions from CREATED to PAID.",
        encoding="utf-8",
    )
    config = AppConfig()
    config.workspace = str(tmp_path)
    config.project.name = "mall-service"
    config.rag.db_path = str(tmp_path / "rag.db")
    service = KnowledgeService(config=config)

    indexed = service.index_local_docs()
    results = service.retriever.retrieve(query="Order status PAID", project="mall-service", doc_type="design_doc", top_k=3, min_score=0.0)

    assert indexed == 1
    assert results
    assert results[0].doc_type == "design_doc"


def test_retrieve_project_docs_for_bug_uses_module_context(tmp_path: Path) -> None:
    order_doc = tmp_path / "docs" / "module" / "order.md"
    payment_doc = tmp_path / "docs" / "module" / "payment.md"
    order_doc.parent.mkdir(parents=True)
    order_doc.write_text(
        "---\nproject: mall-service\nmodule: order\n---\n"
        "# Order Module\n\n"
        "firstItemId handles No value present for order item lookup.",
        encoding="utf-8",
    )
    payment_doc.write_text(
        "---\nproject: mall-service\nmodule: payment\n---\n"
        "# Payment Module\n\n"
        "payment timeout settings are validated at startup.",
        encoding="utf-8",
    )
    config = AppConfig()
    config.workspace = str(tmp_path)
    config.project.name = "mall-service"
    config.rag.db_path = str(tmp_path / "rag.db")
    config.rag.min_score = 0.0
    service = KnowledgeService(config=config)
    service.index_local_docs()
    bug_event = BugEvent(
        bug_id="BUG-DOC",
        source="log",
        project="mall-service",
        title="No value present",
        exception_type="java.util.NoSuchElementException",
        message="No value present",
        frames=[StackFrame(file_path="OrderService.java", function_name="firstItemId", line_number=34, module_name="order")],
        fingerprint="fp",
    )

    results = service.retrieve_project_docs_for_bug(bug_event, session={}, top_k=3)

    assert any(item.module == "order" and "firstItemId" in item.content for item in results)
