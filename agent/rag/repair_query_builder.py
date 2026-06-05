from __future__ import annotations

"""Build separate lexical and semantic repair-time RAG queries."""

from typing import Any

from agent.rag.models import RepairRagQuery


class RepairQueryBuilder:
    """Construct bounded queries while preserving exact Java symbols for BM25."""

    def build(self, context: dict[str, Any]) -> RepairRagQuery:
        symbols = [str(item) for item in context.get("symbols", []) if str(item)]
        modules = [str(item) for item in context.get("module_candidates", []) if str(item)]
        common_exact = self._join(
            context.get("exception_type"),
            context.get("class_name"),
            context.get("method_name"),
            context.get("top_business_frame"),
            context.get("request_path"),
            *modules,
            *symbols,
            context.get("message"),
        )
        passed_skill_bm25 = self._join(common_exact, "successful repair fix pattern")
        passed_skill_vector = self._join(
            "Find historical successful Java repair skills useful for fixing this bug.",
            f"Exception: {context.get('exception_type', '')}.",
            f"Message: {context.get('message', '')}.",
            f"Class: {context.get('class_name', '')}.",
            f"Method: {context.get('method_name', '')}.",
            f"Frame: {context.get('top_business_frame', '')}.",
            f"Modules: {', '.join(modules)}.",
            context.get("root_cause_hint"),
        )
        failed_skill_bm25 = self._join(common_exact, "failed repair avoid mistake rejected")
        failed_skill_vector = self._join(
            "Find historical failed or rejected Java repair skills that reveal mistakes to avoid when fixing this bug.",
            f"Exception: {context.get('exception_type', '')}.",
            f"Message: {context.get('message', '')}.",
            f"Class: {context.get('class_name', '')}.",
            f"Method: {context.get('method_name', '')}.",
            f"Frame: {context.get('top_business_frame', '')}.",
            context.get("root_cause_hint"),
        )
        validation_skill_bm25 = self._join(common_exact, "validation test regression compile")
        validation_skill_vector = self._join(
            "Find historical validation or regression-test hints for verifying the fix of this Java bug.",
            f"Exception: {context.get('exception_type', '')}.",
            f"Message: {context.get('message', '')}.",
            f"Class: {context.get('class_name', '')}.",
            f"Method: {context.get('method_name', '')}.",
        )
        project_doc_bm25 = self._join(common_exact, "api product database validation rule constraint")
        project_doc_vector = self._join(
            "Find current project documents containing business constraints, API rules, database rules, error-code rules, module responsibilities, or validation requirements relevant to this bug.",
            context.get("exception_type"),
            context.get("message"),
            context.get("class_name"),
            context.get("method_name"),
            context.get("request_path"),
            *modules,
        )
        return RepairRagQuery(
            project=str(context.get("project") or ""),
            module_candidates=modules,
            exception_type=str(context.get("exception_type") or ""),
            message=str(context.get("message") or ""),
            class_name=str(context.get("class_name") or ""),
            method_name=str(context.get("method_name") or ""),
            package_name=str(context.get("package_name") or ""),
            top_business_frame=str(context.get("top_business_frame") or ""),
            request_path=str(context.get("request_path") or ""),
            repair_stage=str(context.get("repair_stage") or "before_edit"),
            root_cause_hint=str(context.get("root_cause_hint") or ""),
            skill_bm25_query=passed_skill_bm25,
            skill_vector_query=passed_skill_vector,
            passed_skill_bm25_query=passed_skill_bm25,
            passed_skill_vector_query=passed_skill_vector,
            failed_skill_bm25_query=failed_skill_bm25,
            failed_skill_vector_query=failed_skill_vector,
            validation_skill_bm25_query=validation_skill_bm25,
            validation_skill_vector_query=validation_skill_vector,
            project_doc_bm25_query=project_doc_bm25,
            project_doc_vector_query=project_doc_vector,
        )

    def _join(self, *parts: Any) -> str:
        values = [str(part).strip() for part in parts if str(part or "").strip()]
        return " ".join(dict.fromkeys(values))
