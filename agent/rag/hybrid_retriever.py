from __future__ import annotations

"""Hybrid BM25/vector retrieval with weighted reciprocal-rank fusion."""

from collections import defaultdict
from typing import Callable

from agent.config import RagRetrievalConfig
from agent.ingestion.sanitizer import Sanitizer
from agent.rag.embedder import EmbeddingProvider
from agent.rag.local_doc_loader import LOCAL_DOC_TYPES
from agent.rag.models import RagStatus, RepairRagQuery, RetrievalResult
from agent.rag.vector_store import SQLiteVectorStore


class HybridRetriever:
    """Fuse lexical and semantic recall, then aggregate Skill parents."""

    def __init__(self, store: SQLiteVectorStore, embedder: EmbeddingProvider, config: RagRetrievalConfig) -> None:
        self.store = store
        self.embedder = embedder
        self.config = config
        self.sanitizer = Sanitizer()

    def retrieve_skills(self, query: RepairRagQuery, status: RagStatus | None = None) -> list[RetrievalResult]:
        fused = self._hybrid(
            bm25_query=query.skill_bm25_query,
            vector_query=query.skill_vector_query,
            project=query.project,
            doc_types=["skill"],
            top_k=self.config.skill_child_top_k,
            status=status,
        )
        self._boost_modules(fused, query.module_candidates)
        return self._aggregate_skill_parents(fused, status)[: self.config.parent_skill_top_k]

    def retrieve_project_docs(self, query: RepairRagQuery, status: RagStatus | None = None) -> list[RetrievalResult]:
        fused = self._hybrid(
            bm25_query=query.project_doc_bm25_query,
            vector_query=query.project_doc_vector_query,
            project=query.project,
            doc_types=LOCAL_DOC_TYPES,
            top_k=self.config.project_doc_recall_top_k,
            status=status,
        )
        self._boost_modules(fused, query.module_candidates)
        counts: dict[str, int] = defaultdict(int)
        selected: list[RetrievalResult] = []
        for item in sorted(fused, key=lambda result: result.score, reverse=True):
            if counts[item.doc_id] >= self.config.per_doc_chunk_cap:
                continue
            counts[item.doc_id] += 1
            selected.append(item)
            if len(selected) >= self.config.project_doc_final_top_k:
                break
        return selected

    def _hybrid(
        self,
        *,
        bm25_query: str,
        vector_query: str,
        project: str,
        doc_types: list[str],
        top_k: int,
        status: RagStatus | None,
    ) -> list[RetrievalResult]:
        bm25_results = self._safe_recall(
            "bm25",
            lambda: self.store.search_bm25(query=bm25_query, project=project, doc_types=doc_types, top_k=top_k),
            status,
        )
        vector_results = self._safe_recall(
            "vector",
            lambda: self.store.search(
                query_embedding=self.embedder.embed(vector_query),
                project=project,
                doc_types=doc_types,
                top_k=top_k,
                min_score=self.config.vector_min_score,
            ),
            status,
        )
        return self.weighted_rrf(bm25_results, vector_results)

    def weighted_rrf(
        self,
        bm25_results: list[RetrievalResult],
        vector_results: list[RetrievalResult],
    ) -> list[RetrievalResult]:
        combined: dict[str, RetrievalResult] = {}
        for source, weight, results in [
            ("bm25", self.config.bm25_weight, bm25_results),
            ("vector", self.config.vector_weight, vector_results),
        ]:
            for rank, item in enumerate(results, start=1):
                if item.chunk_id not in combined:
                    combined[item.chunk_id] = item.model_copy(deep=True)
                    combined[item.chunk_id].score = 0.0
                    combined[item.chunk_id].retrieval_sources = []
                    combined[item.chunk_id].ranks = {}
                target = combined[item.chunk_id]
                target.score += weight / (self.config.rrf_k + rank)
                target.retrieval_sources.append(source)
                target.ranks[source] = rank
        return sorted(combined.values(), key=lambda item: (-item.score, item.chunk_id))

    def _aggregate_skill_parents(self, children: list[RetrievalResult], status: RagStatus | None) -> list[RetrievalResult]:
        groups: dict[str, list[RetrievalResult]] = defaultdict(list)
        for child in children:
            groups[child.doc_id].append(child)
        parents: list[RetrievalResult] = []
        for doc_id, matches in groups.items():
            matches.sort(key=lambda item: item.score, reverse=True)
            best = matches[0]
            child_types = list(dict.fromkeys(str(item.metadata.get("child_type") or "") for item in matches if item.metadata.get("child_type")))
            boost = min(max(len(child_types) - 1, 0) * 0.002, 0.004)
            document = self.store.get_document(doc_id)
            if document is None and status is not None:
                status.status = "degraded"
                if "parent_read" not in status.degraded_stages:
                    status.degraded_stages.append("parent_read")
                status.reasons.append(f"Skill parent unavailable: {doc_id}"[:240])
                status.fallback_strategies.append("use_matched_child")
            metadata = {**(document.metadata if document is not None else {}), **best.metadata}
            metadata.update(
                {
                    "matched_child_types": child_types,
                    "matched_chunk_ids": [item.chunk_id for item in matches],
                }
            )
            parents.append(
                RetrievalResult(
                    chunk_id=best.chunk_id,
                    doc_id=doc_id,
                    parent_id=doc_id,
                    source=document.source if document is not None else best.source,
                    doc_type="skill",
                    project=document.project if document is not None else best.project,
                    module=document.module if document is not None else best.module,
                    title=document.title if document is not None else best.title,
                    content=document.content if document is not None else best.content,
                    score=best.score + boost,
                    metadata=metadata,
                    retrieval_sources=list(dict.fromkeys(source for item in matches for source in item.retrieval_sources)),
                    ranks=best.ranks,
                )
            )
        return sorted(parents, key=lambda item: (-item.score, item.doc_id))

    def _safe_recall(
        self,
        stage: str,
        recall: Callable[[], list[RetrievalResult]],
        status: RagStatus | None,
    ) -> list[RetrievalResult]:
        try:
            return recall()
        except Exception as exc:
            if status is not None:
                status.status = "degraded"
                if stage not in status.degraded_stages:
                    status.degraded_stages.append(stage)
                status.reasons.append(self._safe_error(exc))
                status.fallback_strategies.append("vector_only" if stage == "bm25" else "bm25_only")
            return []

    def _boost_modules(self, results: list[RetrievalResult], modules: list[str]) -> None:
        module_set = set(modules)
        for item in results:
            if item.module and item.module in module_set:
                item.score += 0.001
        results.sort(key=lambda item: (-item.score, item.chunk_id))

    def _safe_error(self, exc: Exception) -> str:
        message = " ".join(self.sanitizer.sanitize(str(exc)).split())
        return message[:240]
