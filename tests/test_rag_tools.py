from __future__ import annotations

from agent.config import AppConfig
from agent.rag.models import RetrievalResult
from agent.tools.base import PermissionType
from agent.tools.search_project_doc import SearchProjectDocTool
from agent.tools.search_skill import SearchSkillTool


class FakeRetriever:
    def __init__(self, results: list[RetrievalResult]) -> None:
        self.results = results
        self.calls: list[dict[str, object]] = []

    def retrieve(self, **kwargs):
        self.calls.append(kwargs)
        doc_types = kwargs.get("doc_type")
        if isinstance(doc_types, str):
            doc_types = [doc_types]
        if doc_types:
            return [item for item in self.results if item.doc_type in doc_types]
        return self.results


class FakeKnowledgeService:
    def __init__(self, results: list[RetrievalResult]) -> None:
        self.retriever = FakeRetriever(results)


def _result(*, title: str, doc_type: str, module: str, score: float = 0.9, content: str = "content") -> RetrievalResult:
    return RetrievalResult(
        chunk_id=f"chunk:{title}",
        doc_id=f"doc:{title}",
        source="skill" if doc_type == "skill" else "local_doc",
        doc_type=doc_type,
        project="mall-service",
        module=module,
        title=title,
        content=content,
        score=score,
        metadata={"module": module, "title": title},
    )


def test_search_skill_schema_is_read_only() -> None:
    tool = SearchSkillTool(AppConfig(), FakeKnowledgeService([]))  # type: ignore[arg-type]
    schema = tool.spec.input_schema

    assert tool.permission == PermissionType.READ_ONLY
    assert tool.spec.permission == "READ_ONLY"
    assert schema["required"] == ["query", "project"]
    assert set(schema["properties"]) == {"query", "project", "module", "exception_type", "top_k"}
    assert schema["additionalProperties"] is False


def test_search_project_doc_schema_is_read_only() -> None:
    tool = SearchProjectDocTool(AppConfig(), FakeKnowledgeService([]))  # type: ignore[arg-type]
    schema = tool.spec.input_schema

    assert tool.permission == PermissionType.READ_ONLY
    assert tool.spec.permission == "READ_ONLY"
    assert schema["required"] == ["query", "project"]
    assert set(schema["properties"]) == {"query", "project", "module", "doc_types", "top_k"}
    assert schema["properties"]["doc_types"]["items"]["type"] == "string"
    assert schema["additionalProperties"] is False


def test_search_skill_returns_top_k_and_limits_content() -> None:
    config = AppConfig()
    config.project.name = "mall-service"
    config.rag.min_score = 0.0
    service = FakeKnowledgeService(
        [
            _result(title="Skill One", doc_type="skill", module="order", content="a" * 50),
            _result(title="Skill Two", doc_type="skill", module="order", content="b" * 50),
            _result(title="Skill Three", doc_type="skill", module="payment", content="c" * 50),
        ]
    )
    tool = SearchSkillTool(config, service, max_content_chars=12)  # type: ignore[arg-type]

    result = tool.run({"query": "NoSuchElementException firstItemId", "project": "mall-service", "module": "order", "top_k": 1})

    assert result.success is True
    assert result.stdout_summary == "found 1 result(s)"
    assert len(result.data["results"]) == 1
    assert result.data["results"][0]["title"] == "Skill One"
    assert result.data["results"][0]["content"] == "a" * 12 + "..."
    assert service.retriever.calls[0]["doc_type"] == "skill"
    assert service.retriever.calls[0]["top_k"] == 4


def test_search_project_doc_supports_doc_types_filter() -> None:
    config = AppConfig()
    config.project.name = "mall-service"
    config.rag.min_score = 0.0
    service = FakeKnowledgeService(
        [
            _result(title="Order API", doc_type="api_doc", module="order"),
            _result(title="Order DB", doc_type="db_doc", module="order"),
            _result(title="Payment API", doc_type="api_doc", module="payment"),
        ]
    )
    tool = SearchProjectDocTool(config, service)  # type: ignore[arg-type]

    result = tool.run({"query": "create order amount", "project": "mall-service", "module": "order", "doc_types": ["api_doc"], "top_k": 5})

    assert result.success is True
    assert result.stdout_summary == "found 1 result(s)"
    assert [item["title"] for item in result.data["results"]] == ["Order API"]
    assert service.retriever.calls[0]["doc_type"] == ["api_doc"]
    assert service.retriever.calls[0]["project"] == "mall-service"
