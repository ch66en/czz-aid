from __future__ import annotations

"""Rerank providers for repair-time RAG candidates."""

from typing import Protocol

import requests

from agent.config import AppConfig
from agent.rag.models import RetrievalResult


class RerankerProvider(Protocol):
    def rerank(
        self,
        *,
        query: str,
        candidates: list[RetrievalResult],
        top_n: int,
        instruct: str,
        min_score: float,
        min_keep: int,
        query_type: str,
    ) -> list[RetrievalResult]:
        ...


class QwenOpenAICompatibleReranker:
    """DashScope Qwen rerank through its OpenAI-compatible HTTP endpoint."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: int = 30,
        document_max_chars: int = 2000,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.document_max_chars = document_max_chars

    def rerank(
        self,
        *,
        query: str,
        candidates: list[RetrievalResult],
        top_n: int,
        instruct: str,
        min_score: float,
        min_keep: int,
        query_type: str,
    ) -> list[RetrievalResult]:
        if not query.strip() or not candidates:
            return candidates

        documents = [self._document_text(item) for item in candidates]
        response = requests.post(
            f"{self.base_url}/reranks",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "query": query,
                "documents": documents,
                "top_n": min(top_n, len(documents)),
                "instruct": instruct,
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()

        ranked: list[RetrievalResult] = []
        for rank, raw in enumerate(payload.get("results", []), start=1):
            index = int(raw["index"])
            if index < 0 or index >= len(candidates):
                continue
            score = float(raw["relevance_score"])
            candidate = candidates[index].model_copy(deep=True)
            candidate.metadata["pre_rerank_score"] = candidate.score
            candidate.metadata["pre_rerank_rank"] = index + 1
            candidate.metadata["rerank_score"] = score
            candidate.metadata["rerank_rank"] = rank
            candidate.metadata["rerank_model"] = self.model
            candidate.metadata["rerank_query_type"] = query_type
            candidate.score = score
            ranked.append(candidate)

        filtered = [item for item in ranked if float(item.metadata.get("rerank_score", 0.0)) >= min_score]
        if not filtered and ranked and min_keep > 0:
            filtered = ranked[:min_keep]
            for item in filtered:
                item.metadata["rerank_below_threshold"] = True
        return filtered

    def _document_text(self, item: RetrievalResult) -> str:
        title = item.title or ""
        module = item.module or ""
        child_type = str(item.metadata.get("child_type") or "")
        section_name = str(item.metadata.get("section_name") or "")
        text = "\n".join(
            part
            for part in [
                f"Title: {title}",
                f"Module: {module}" if module else "",
                f"Chunk type: {child_type}" if child_type else "",
                f"Section: {section_name}" if section_name else "",
                item.content,
            ]
            if part
        ).strip()
        return text[: self.document_max_chars]


def build_reranker_provider(config: AppConfig) -> RerankerProvider | None:
    rerank = config.rag.rerank
    if not rerank.enabled:
        return None

    provider = rerank.provider.strip().lower()
    api_key = rerank.api_key.strip() or config.rag.embedding_api_key.strip() or config.llm.api_key.strip()
    if provider == "qwen_openai_compatible" and api_key and rerank.model.strip():
        return QwenOpenAICompatibleReranker(
            api_key=api_key,
            base_url=rerank.base_url,
            model=rerank.model,
            timeout_seconds=rerank.timeout_seconds,
            document_max_chars=rerank.document_max_chars,
        )
    return None
