from __future__ import annotations

"""Build a bounded, source-validated repair context from RAG candidates."""

import json
import re
import time
from typing import Any

from agent.config import RagContextSynthesizerConfig
from agent.llm.openai_compatible_client import OpenAICompatibleClient
from agent.rag.models import (
    KnowledgeReference,
    RagContextItem,
    RagRepairContext,
    RagStatus,
    RepairRagQuery,
    RetrievalResult,
)


class ContextSynthesizer:
    """Classify retrieved evidence without allowing invented sources."""

    def __init__(
        self,
        config: RagContextSynthesizerConfig,
        llm_client: OpenAICompatibleClient | None = None,
    ) -> None:
        self.config = config
        self.llm_client = llm_client

    def synthesize(
        self,
        query: RepairRagQuery,
        skills: list[RetrievalResult],
        project_docs: list[RetrievalResult],
        status: RagStatus | None = None,
    ) -> RagRepairContext:
        candidates = [*skills, *project_docs]
        if not self.config.enabled or self.llm_client is None:
            if status is not None:
                if status.status != "failed":
                    status.status = "degraded"
                if "context_synthesizer" not in status.degraded_stages:
                    status.degraded_stages.append("context_synthesizer")
                status.reasons.append("context synthesizer is disabled or has no configured LLM")
                status.fallback_strategies.append("deterministic_synthesizer")
            return self.deterministic_fallback(query, skills, project_docs)

        start = time.perf_counter()
        prompt = self._prompt(query, candidates)
        for attempt in range(2):
            messages = [{"role": "system", "content": prompt}]
            if attempt:
                messages.append({"role": "user", "content": "Return one valid JSON object only. Preserve only supplied source references."})
            try:
                response = self.llm_client.chat(
                    messages,
                    response_format={"type": "json_object"},
                    max_tokens=self.config.max_output_tokens,
                    persist_call_record=False,
                )
                if not response.success:
                    continue
                raw = str(response.data.get("content") or "")
                context = RagRepairContext.model_validate(self._parse_json(raw))
                context = self._validate_sources(context, candidates)
                if status is not None:
                    status.synthesizer_latency_ms = int((time.perf_counter() - start) * 1000)
                return context
            except Exception:
                continue
        if status is not None:
            if status.status != "failed":
                status.status = "degraded"
            status.degraded_stages.append("context_synthesizer")
            status.reasons.append("context synthesizer output validation failed")
            status.fallback_strategies.append("deterministic_synthesizer")
            status.synthesizer_latency_ms = int((time.perf_counter() - start) * 1000)
        return self.deterministic_fallback(query, skills, project_docs)

    def deterministic_fallback(
        self,
        query: RepairRagQuery,
        skills: list[RetrievalResult],
        project_docs: list[RetrievalResult],
    ) -> RagRepairContext:
        context = RagRepairContext(query_summary=self._query_summary(query))
        for item in project_docs:
            context_item = self._item(item, use_type="business_constraint")
            if str(item.metadata.get("authority") or "unclassified") == "approved":
                context.hard_constraints.append(context_item)
            else:
                context_item.confidence = "low" if item.metadata.get("authority") == "unclassified" else "medium"
                context.soft_hints.append(context_item)
        for item in skills:
            skill_type = str(item.metadata.get("skill_type") or "legacy_unclassified")
            use_types = [str(value) for value in item.metadata.get("use_types", [])] if isinstance(item.metadata.get("use_types"), list) else []
            if skill_type == "review_passed":
                context.soft_hints.append(self._item(item, use_type="recommended_fix"))
                if "validation_hint" in use_types:
                    context.validation_hints.append(self._item(item, use_type="validation_hint"))
            elif skill_type == "review_failed":
                context.avoid_patterns.append(self._item(item, use_type="avoid_pattern"))
                if "validation_hint" in use_types:
                    context.validation_hints.append(self._item(item, use_type="validation_hint"))
            else:
                context.soft_hints.append(self._item(item, confidence="low", use_type="debug_hint"))
        context.conflicts = self._document_conflicts(project_docs)
        context.selected_sources = self._selected_sources(context)
        context.confidence = "high" if context.hard_constraints else ("medium" if context.soft_hints or context.avoid_patterns else "low")
        if not context.selected_sources:
            context.missing_info.append("No relevant indexed knowledge was selected.")
        return context

    def _validate_sources(self, context: RagRepairContext, candidates: list[RetrievalResult]) -> RagRepairContext:
        by_key = {(item.doc_id, item.chunk_id, item.parent_id): item for item in candidates}

        def valid_items(items: list[RagContextItem], *, hard: bool = False) -> list[RagContextItem]:
            result: list[RagContextItem] = []
            for item in items:
                key = (item.source.doc_id, item.source.chunk_id, item.source.parent_id)
                candidate = by_key.get(key)
                if candidate is None:
                    continue
                if hard and (candidate.doc_type == "skill" or candidate.metadata.get("authority") != "approved"):
                    continue
                skill_type = str(candidate.metadata.get("skill_type") or "")
                if item.use_type == "recommended_fix" and skill_type != "review_passed":
                    continue
                item.text = self._safe_text(item.text)
                result.append(item)
            return result

        context.hard_constraints = valid_items(context.hard_constraints, hard=True)
        context.soft_hints = valid_items(context.soft_hints)
        context.avoid_patterns = valid_items(context.avoid_patterns)
        context.validation_hints = valid_items(context.validation_hints)
        context.selected_sources = self._selected_sources(context)
        return context

    def _item(self, result: RetrievalResult, *, confidence: str = "medium", use_type: str) -> RagContextItem:
        return RagContextItem(
            text=self._safe_text(result.content),
            source=self._reference(result),
            confidence=confidence,
            use_type=use_type,
        )

    def _reference(self, result: RetrievalResult) -> KnowledgeReference:
        return KnowledgeReference(
            source=result.source,
            source_type="skill" if result.doc_type == "skill" else "project_doc",
            doc_id=result.doc_id,
            chunk_id=result.chunk_id,
            parent_id=result.parent_id,
            title=result.title,
            uri=str(result.metadata.get("uri") or result.metadata.get("doc_path") or ""),
        )

    def _selected_sources(self, context: RagRepairContext) -> list[KnowledgeReference]:
        refs: list[KnowledgeReference] = []
        seen: set[tuple[str, str, str]] = set()
        for item in [*context.hard_constraints, *context.soft_hints, *context.avoid_patterns, *context.validation_hints]:
            key = (item.source.doc_id, item.source.chunk_id, item.source.parent_id)
            if key not in seen:
                refs.append(item.source)
                seen.add(key)
        return refs

    def _prompt(self, query: RepairRagQuery, candidates: list[RetrievalResult]) -> str:
        payload = [
            {
                "doc_id": item.doc_id,
                "chunk_id": item.chunk_id,
                "parent_id": item.parent_id,
                "doc_type": item.doc_type,
                "title": item.title,
                "content": item.content[:1800],
                "metadata": item.metadata,
            }
            for item in candidates
        ]
        return (
            "Create a RagRepairContext JSON object from supplied candidates. Do not generate a patch. "
            "Only approved project documents may be hard_constraints. Never invent source identifiers. "
            f"QUERY: {query.model_dump_json()}\nCANDIDATES: {json.dumps(payload, ensure_ascii=False, default=str)}"
        )

    def _parse_json(self, raw: str) -> dict[str, Any]:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, flags=re.S)
            if not match:
                raise
            value = json.loads(match.group(0))
        if not isinstance(value, dict):
            raise ValueError("synthesizer output must be an object")
        return value

    def _safe_text(self, text: str, limit: int = 1800) -> str:
        lines = [
            line
            for line in str(text).splitlines()
            if not re.match(r"^(diff --git|index [0-9a-f]+|--- a/|\+\+\+ b/|@@ )", line)
        ]
        compact = "\n".join(lines).strip()
        return compact if len(compact) <= limit else f"{compact[:limit]}..."

    def _query_summary(self, query: RepairRagQuery) -> str:
        parts = [query.exception_type, query.class_name, query.method_name, query.message]
        return " | ".join(part for part in parts if part)[:800]

    def _document_conflicts(self, project_docs: list[RetrievalResult]) -> list[dict[str, Any]]:
        groups: dict[tuple[str, str, str], list[RetrievalResult]] = {}
        for item in project_docs:
            if item.metadata.get("authority") != "approved":
                continue
            key = (item.module, item.doc_type, item.title.lower())
            groups.setdefault(key, []).append(item)
        conflicts: list[dict[str, Any]] = []
        for (module, doc_type, title), items in groups.items():
            contents = {item.content.strip() for item in items}
            if len(items) > 1 and len(contents) > 1:
                conflicts.append(
                    {
                        "module": module,
                        "doc_type": doc_type,
                        "title": title,
                        "source_doc_ids": [item.doc_id for item in items],
                    }
                )
        return conflicts
