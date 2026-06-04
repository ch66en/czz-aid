from __future__ import annotations

from pathlib import Path

from agent.config import RagRetrievalConfig
from agent.models import BugEvent, StackFrame
from agent.rag.embedder import DeterministicEmbeddingProvider
from agent.rag.hybrid_retriever import HybridRetriever
from agent.rag.models import RagStatus, RepairRagQuery, RetrievalResult
from agent.rag.repair_context_resolver import RepairContextResolver
from agent.rag.repair_query_builder import RepairQueryBuilder


def _result(name: str, score: float, *, doc_id: str | None = None, doc_type: str = "skill", module: str = "") -> RetrievalResult:
    return RetrievalResult(
        chunk_id=f"chunk:{name}",
        doc_id=doc_id or f"doc:{name}",
        source="skill" if doc_type == "skill" else "local_doc",
        doc_type=doc_type,
        project="demo",
        module=module,
        title=name,
        content=name,
        score=score,
        metadata={"child_type": name},
    )


class FakeStore:
    def __init__(self, bm25=None, vector=None, *, bm25_error: Exception | None = None, vector_error: Exception | None = None) -> None:
        self.bm25 = bm25 or []
        self.vector = vector or []
        self.bm25_error = bm25_error
        self.vector_error = vector_error

    def search_bm25(self, **kwargs):
        if self.bm25_error:
            raise self.bm25_error
        return self.bm25

    def search(self, **kwargs):
        if self.vector_error:
            raise self.vector_error
        return self.vector

    def get_document(self, doc_id):
        return None


def test_context_resolver_extracts_symbols_without_treating_fqcn_as_module() -> None:
    bug = BugEvent(
        bug_id="B",
        source="log",
        project="demo",
        title="",
        exception_type="NullPointerException",
        message="boom",
        frames=[StackFrame(file_path="OrderService.java", function_name="createOrder", line_number=7, module_name="com.example.order.OrderService", is_business_code=True)],
        fingerprint="fp",
    )

    resolved = RepairContextResolver({"com.example.order": "order"}).resolve(bug, {})

    assert resolved["class_name"] == "OrderService"
    assert resolved["method_name"] == "createOrder"
    assert resolved["package_name"] == "com.example.order"
    assert resolved["module_candidates"] == ["order"]
    assert "com.example.order.OrderService" not in resolved["module_candidates"]


def test_query_builder_keeps_java_symbols_for_bm25() -> None:
    query = RepairQueryBuilder().build(
        {
            "project": "demo",
            "exception_type": "java.lang.NullPointerException",
            "message": "order missing",
            "class_name": "OrderService",
            "method_name": "createOrder",
            "symbols": ["OrderService#createOrder"],
        }
    )

    assert "java.lang.NullPointerException" in query.skill_bm25_query
    assert "OrderService#createOrder" in query.skill_bm25_query


def test_weighted_rrf_uses_rank_not_raw_score() -> None:
    retriever = HybridRetriever(FakeStore(), DeterministicEmbeddingProvider(), RagRetrievalConfig(bm25_weight=1.0, vector_weight=1.0, rrf_k=10))  # type: ignore[arg-type]
    a = _result("a", 0.01)
    b = _result("b", 999.0)

    fused = retriever.weighted_rrf([a, b], [])

    assert [item.title for item in fused] == ["a", "b"]
    assert fused[0].score == 1 / 11


def test_hybrid_retriever_merges_sources_and_vector_threshold_does_not_filter_rrf() -> None:
    a_bm25 = _result("a", 0.0)
    a_vector = _result("a", 0.26)
    store = FakeStore(bm25=[a_bm25], vector=[a_vector])
    retriever = HybridRetriever(store, DeterministicEmbeddingProvider(), RagRetrievalConfig(vector_min_score=0.9))  # type: ignore[arg-type]

    results = retriever._hybrid(bm25_query="a", vector_query="a", project="demo", doc_types=["skill"], top_k=5, status=RagStatus())

    assert results
    assert set(results[0].retrieval_sources) == {"bm25", "vector"}


def test_project_doc_per_doc_cap() -> None:
    results = [
        _result("a1", 1.0, doc_id="doc:a", doc_type="api_doc"),
        _result("a2", 0.9, doc_id="doc:a", doc_type="api_doc"),
        _result("b1", 0.8, doc_id="doc:b", doc_type="api_doc"),
    ]
    retriever = HybridRetriever(FakeStore(bm25=results), DeterministicEmbeddingProvider(), RagRetrievalConfig(per_doc_chunk_cap=1, project_doc_final_top_k=5))  # type: ignore[arg-type]

    selected = retriever.retrieve_project_docs(RepairRagQuery(project="demo", project_doc_bm25_query="x", project_doc_vector_query="x"))

    assert [item.doc_id for item in selected] == ["doc:a", "doc:b"]


def test_retrieval_failures_degrade_independently_and_total_failure_is_visible() -> None:
    status = RagStatus()
    retriever = HybridRetriever(
        FakeStore(bm25_error=RuntimeError("fts failed"), vector_error=RuntimeError("embed failed")),
        DeterministicEmbeddingProvider(),
        RagRetrievalConfig(),
    )  # type: ignore[arg-type]

    results = retriever._hybrid(bm25_query="x", vector_query="x", project="demo", doc_types=["skill"], top_k=5, status=status)

    assert results == []
    assert set(status.degraded_stages) == {"bm25", "vector"}
    assert set(status.fallback_strategies) == {"vector_only", "bm25_only"}
