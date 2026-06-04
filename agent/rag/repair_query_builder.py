from __future__ import annotations

"""Build separate lexical and semantic repair-time RAG queries."""

from typing import Any

from agent.rag.models import RepairRagQuery


class RepairQueryBuilder:
    """Construct bounded queries while preserving exact Java symbols for BM25."""

    def build(self, context: dict[str, Any]) -> RepairRagQuery:
        symbols = [str(item) for item in context.get("symbols", []) if str(item)]
        common_exact = self._join(
            context.get("exception_type"),
            context.get("class_name"),
            context.get("method_name"),
            context.get("top_business_frame"),
            context.get("request_path"),
            *symbols,
            context.get("message"),
        )
        skill_vector = self._join(
            "Historical repair experience for",
            context.get("exception_type"),
            context.get("message"),
            f"class {context.get('class_name', '')}",
            f"method {context.get('method_name', '')}",
            context.get("root_cause_hint"),
        )
        doc_vector = self._join(
            "Business constraints and project documentation for",
            context.get("message"),
            context.get("request_path"),
            *context.get("module_candidates", []),
            context.get("class_name"),
            context.get("method_name"),
        )
        return RepairRagQuery(
            project=str(context.get("project") or ""),
            module_candidates=[str(item) for item in context.get("module_candidates", []) if str(item)],
            exception_type=str(context.get("exception_type") or ""),
            message=str(context.get("message") or ""),
            class_name=str(context.get("class_name") or ""),
            method_name=str(context.get("method_name") or ""),
            package_name=str(context.get("package_name") or ""),
            top_business_frame=str(context.get("top_business_frame") or ""),
            request_path=str(context.get("request_path") or ""),
            repair_stage=str(context.get("repair_stage") or "before_edit"),
            root_cause_hint=str(context.get("root_cause_hint") or ""),
            skill_bm25_query=common_exact,
            skill_vector_query=skill_vector,
            project_doc_bm25_query=self._join(common_exact, *context.get("module_candidates", [])),
            project_doc_vector_query=doc_vector,
        )

    def _join(self, *parts: Any) -> str:
        values = [str(part).strip() for part in parts if str(part or "").strip()]
        return " ".join(dict.fromkeys(values))
