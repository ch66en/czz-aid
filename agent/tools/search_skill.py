from __future__ import annotations

"""Read-only local Skill RAG search tool."""

from typing import Any

from agent.config import AppConfig
from agent.models import ToolResult, ToolSpec
from agent.rag.knowledge_service import KnowledgeService
from agent.rag.models import RagStatus, RepairRagQuery, RetrievalResult
from agent.tools.base import BaseTool, PermissionType


class SearchSkillTool(BaseTool):
    """Search indexed local repair skills without touching external systems."""

    def __init__(self, config: AppConfig, knowledge_service: KnowledgeService | None = None, *, max_content_chars: int = 1200) -> None:
        self.config = config
        self.knowledge_service = knowledge_service or KnowledgeService(config=config)
        self.max_content_chars = max_content_chars

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="search_skill",
            description="Search local indexed repair skills for similar exceptions, modules, or prior fixes. Read-only; does not sync Feishu or update the index.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language or code-oriented search query."},
                    "project": {"type": "string", "description": "Project name to search within."},
                    "module": {"type": "string", "description": "Optional module name to filter returned skills."},
                    "exception_type": {"type": "string", "description": "Optional exception class to add to the search query."},
                    "top_k": {"type": "integer", "description": "Maximum number of results to return. Defaults to configured skill top-k, max 10."},
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
        exception_type = str(data.get("exception_type") or "").strip()
        top_k = self._bounded_int(data.get("top_k"), default=self.config.rag.top_k_skills, minimum=1, maximum=10)
        if not query:
            return self._result([], query=query, project=project, module=module, top_k=top_k)
        if not self.config.rag.enabled:
            return self._result([], query=query, project=project, module=module, top_k=top_k, disabled=True)

        search_query = " ".join(part for part in [query, exception_type, module] if part)
        try:
            if hasattr(self.knowledge_service, "hybrid_retriever"):
                raw_results = self.knowledge_service.hybrid_retriever.retrieve_skills(
                    RepairRagQuery(
                        project=project,
                        module_candidates=[module] if module else [],
                        exception_type=exception_type,
                        skill_bm25_query=search_query,
                        skill_vector_query=search_query,
                    ),
                    RagStatus(),
                )
            else:
                raw_results = self.knowledge_service.retriever.retrieve(
                    query=search_query,
                    project=project,
                    doc_type="skill",
                    top_k=max(top_k * 4, top_k),
                    min_score=self.config.rag.min_score,
                )
        except Exception as exc:
            return ToolResult(tool="search_skill", success=False, exit_code=1, stdout_summary="", stderr_summary=str(exc), data={"query": query, "project": project}, artifacts=[])

        results = self._filter_module(raw_results, module)[:top_k]
        return self._result(results, query=query, project=project, module=module, top_k=top_k)

    def _result(
        self,
        results: list[RetrievalResult],
        *,
        query: str,
        project: str,
        module: str,
        top_k: int,
        disabled: bool = False,
    ) -> ToolResult:
        return ToolResult(
            tool="search_skill",
            success=True,
            exit_code=0,
            stdout_summary=f"found {len(results)} result(s)",
            stderr_summary="RAG disabled" if disabled else "",
            data={
                "query": query,
                "project": project,
                "module": module,
                "top_k": top_k,
                "results": [self._serialize_result(item) for item in results],
            },
            artifacts=[],
        )

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
