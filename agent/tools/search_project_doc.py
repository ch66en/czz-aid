from __future__ import annotations

"""Read-only local project document RAG search tool."""

from typing import Any

from agent.config import AppConfig
from agent.models import ToolResult, ToolSpec
from agent.rag.knowledge_service import KnowledgeService
from agent.rag.local_doc_loader import LOCAL_DOC_TYPES
from agent.rag.models import RetrievalResult
from agent.tools.base import BaseTool, PermissionType


class SearchProjectDocTool(BaseTool):
    """Search indexed local business/project documents."""

    def __init__(self, config: AppConfig, knowledge_service: KnowledgeService | None = None, *, max_content_chars: int = 1200) -> None:
        self.config = config
        self.knowledge_service = knowledge_service or KnowledgeService(config=config)
        self.max_content_chars = max_content_chars

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="search_project_doc",
            description=(
                "Search local indexed project documents such as API contracts, product rules, design docs, database docs, "
                "error-code specs, and module responsibility docs. Read-only; does not sync Feishu or update the index."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language or code-oriented search query."},
                    "project": {"type": "string", "description": "Project name to search within."},
                    "module": {"type": "string", "description": "Optional module name to filter returned documents."},
                    "doc_types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional document type filter, e.g. api_doc, design_doc, db_doc, error_code_doc, module_doc, product_doc.",
                    },
                    "top_k": {"type": "integer", "description": "Maximum number of results to return. Defaults to 5, max 10."},
                },
                "required": ["query", "project"],
                "additionalProperties": False,
            },
            permission=PermissionType.READ_ONLY.value,
            executor="local",
        )

    @property
    def permission(self) -> PermissionType:
        return PermissionType.READ_ONLY

    def run(self, payload: dict[str, Any] | None = None) -> ToolResult:
        data = payload or {}
        query = str(data.get("query") or "").strip()
        project = str(data.get("project") or self.config.project.name).strip()
        module = str(data.get("module") or "").strip()
        doc_types = self._normalize_doc_types(data.get("doc_types"))
        top_k = self._bounded_int(data.get("top_k"), default=5, minimum=1, maximum=10)
        if not query:
            return self._result([], query=query, project=project, module=module, doc_types=doc_types, top_k=top_k)
        if not self.config.rag.enabled:
            return self._result([], query=query, project=project, module=module, doc_types=doc_types, top_k=top_k, disabled=True)

        search_query = " ".join(part for part in [query, module] if part)
        try:
            raw_results = self.knowledge_service.retriever.retrieve(
                query=search_query,
                project=project,
                doc_type=doc_types or LOCAL_DOC_TYPES,
                top_k=max(top_k * 4, top_k),
                min_score=self.config.rag.min_score,
            )
        except Exception as exc:
            return ToolResult(
                tool="search_project_doc",
                success=False,
                exit_code=1,
                stdout_summary="",
                stderr_summary=str(exc),
                data={"query": query, "project": project, "doc_types": doc_types},
                artifacts=[],
            )

        results = self._filter_module(raw_results, module)[:top_k]
        return self._result(results, query=query, project=project, module=module, doc_types=doc_types, top_k=top_k)

    def _result(
        self,
        results: list[RetrievalResult],
        *,
        query: str,
        project: str,
        module: str,
        doc_types: list[str],
        top_k: int,
        disabled: bool = False,
    ) -> ToolResult:
        return ToolResult(
            tool="search_project_doc",
            success=True,
            exit_code=0,
            stdout_summary=f"found {len(results)} result(s)",
            stderr_summary="RAG disabled" if disabled else "",
            data={
                "query": query,
                "project": project,
                "module": module,
                "doc_types": doc_types,
                "top_k": top_k,
                "results": [self._serialize_result(item) for item in results],
            },
            artifacts=[],
        )

    def _normalize_doc_types(self, raw: Any) -> list[str]:
        if not isinstance(raw, list):
            return []
        allowed = set(LOCAL_DOC_TYPES)
        doc_types: list[str] = []
        for item in raw:
            doc_type = str(item or "").strip()
            if doc_type in allowed and doc_type not in doc_types:
                doc_types.append(doc_type)
        return doc_types

    def _filter_module(self, results: list[RetrievalResult], module: str) -> list[RetrievalResult]:
        if not module:
            return results
        expected = module.lower()
        return [item for item in results if item.module.lower() == expected or str(item.metadata.get("module", "")).lower() == expected]

    def _serialize_result(self, result: RetrievalResult) -> dict[str, Any]:
        return {
            "title": result.title,
            "score": round(float(result.score), 4),
            "source": result.source,
            "doc_type": result.doc_type,
            "module": result.module,
            "content": self._truncate(result.content),
            "metadata": result.metadata,
        }

    def _truncate(self, content: str) -> str:
        text = str(content)
        if len(text) <= self.max_content_chars:
            return text
        return f"{text[: self.max_content_chars]}..."

    def _bounded_int(self, value: Any, *, default: int, minimum: int, maximum: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return default
        return max(minimum, min(maximum, number))
