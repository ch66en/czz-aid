from __future__ import annotations

import pytest

from agent.rag.models import RetrievalResult
from agent.rag.reranker import QwenOpenAICompatibleReranker


def _result(name: str, score: float = 0.1, *, content: str | None = None) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=f"chunk:{name}",
        doc_id=f"doc:{name}",
        source="skill",
        doc_type="skill",
        project="demo",
        title=name,
        content=content or f"{name} content",
        score=score,
        metadata={"child_type": "fix_chunk"},
    )


class FakeResponse:
    def __init__(self, payload: dict[str, object] | None = None, *, error: Exception | None = None) -> None:
        self.payload = payload or {}
        self.error = error

    def raise_for_status(self) -> None:
        if self.error is not None:
            raise self.error

    def json(self) -> dict[str, object]:
        return self.payload


def test_qwen_reranker_reorders_candidates_and_writes_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return FakeResponse(
            {
                "results": [
                    {"index": 1, "relevance_score": 0.92},
                    {"index": 0, "relevance_score": 0.51},
                ]
            }
        )

    monkeypatch.setattr("agent.rag.reranker.requests.post", fake_post)
    reranker = QwenOpenAICompatibleReranker(api_key="secret", base_url="https://dashscope.aliyuncs.com/compatible-api/v1", model="qwen3-rerank")

    ranked = reranker.rerank(
        query="fix npe",
        candidates=[_result("a", 0.1), _result("b", 0.2)],
        top_n=30,
        instruct="rank",
        min_score=0.2,
        min_keep=1,
        query_type="passed_skill",
    )

    assert [item.doc_id for item in ranked] == ["doc:b", "doc:a"]
    assert ranked[0].score == 0.92
    assert ranked[0].metadata["pre_rerank_score"] == 0.2
    assert ranked[0].metadata["pre_rerank_rank"] == 2
    assert ranked[0].metadata["rerank_rank"] == 1
    assert ranked[0].metadata["rerank_model"] == "qwen3-rerank"
    assert ranked[0].metadata["rerank_query_type"] == "passed_skill"
    assert calls[0]["url"] == "https://dashscope.aliyuncs.com/compatible-api/v1/reranks"
    assert calls[0]["timeout"] == 30


def test_qwen_reranker_min_keep_marks_below_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url, **kwargs):
        return FakeResponse({"results": [{"index": 0, "relevance_score": 0.01}]})

    monkeypatch.setattr("agent.rag.reranker.requests.post", fake_post)
    reranker = QwenOpenAICompatibleReranker(api_key="secret", base_url="https://example.test", model="qwen3-rerank")

    ranked = reranker.rerank(
        query="fix npe",
        candidates=[_result("a", 0.1)],
        top_n=1,
        instruct="rank",
        min_score=0.9,
        min_keep=1,
        query_type="project_doc",
    )

    assert len(ranked) == 1
    assert ranked[0].metadata["rerank_below_threshold"] is True


def test_qwen_reranker_truncates_document_text(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs)
        return FakeResponse({"results": [{"index": 0, "relevance_score": 1.0}]})

    monkeypatch.setattr("agent.rag.reranker.requests.post", fake_post)
    reranker = QwenOpenAICompatibleReranker(api_key="secret", base_url="https://example.test", model="qwen3-rerank", document_max_chars=80)

    reranker.rerank(
        query="fix npe",
        candidates=[_result("a", content="x" * 500)],
        top_n=1,
        instruct="rank",
        min_score=0,
        min_keep=1,
        query_type="project_doc",
    )

    documents = captured["json"]["documents"]  # type: ignore[index]
    assert len(documents[0]) == 80


def test_qwen_reranker_http_error_is_propagated(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url, **kwargs):
        return FakeResponse(error=RuntimeError("server failed"))

    monkeypatch.setattr("agent.rag.reranker.requests.post", fake_post)
    reranker = QwenOpenAICompatibleReranker(api_key="secret", base_url="https://example.test", model="qwen3-rerank")

    with pytest.raises(RuntimeError, match="server failed"):
        reranker.rerank(
            query="fix npe",
            candidates=[_result("a")],
            top_n=1,
            instruct="rank",
            min_score=0,
            min_keep=1,
            query_type="project_doc",
        )
