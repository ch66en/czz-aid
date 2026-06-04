from __future__ import annotations

import json

from agent.config import RagContextSynthesizerConfig
from agent.models import ToolResult
from agent.rag.context_synthesizer import ContextSynthesizer
from agent.rag.models import RagStatus, RepairRagQuery, RetrievalResult


def _candidate(name: str, *, doc_type: str, authority: str = "", skill_type: str = "") -> RetrievalResult:
    metadata = {"authority": authority} if authority else {}
    if skill_type:
        metadata.update({"skill_type": skill_type, "use_types": ["recommended_fix", "validation_hint"]})
    return RetrievalResult(
        chunk_id=f"chunk:{name}",
        doc_id=f"doc:{name}",
        parent_id=f"doc:{name}" if doc_type == "skill" else "",
        source="skill" if doc_type == "skill" else "local_doc",
        doc_type=doc_type,
        project="demo",
        title=name,
        content=f"diff --git a/X b/X\n{name} guidance",
        score=1.0,
        metadata=metadata,
    )


class FakeLLM:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return ToolResult(tool="llm_chat", success=True, exit_code=0, data={"content": self.content})


def test_unapproved_doc_is_not_hard_constraint_and_patch_header_is_removed() -> None:
    synthesizer = ContextSynthesizer(RagContextSynthesizerConfig(enabled=False))
    approved = _candidate("approved", doc_type="api_doc", authority="approved")
    draft = _candidate("draft", doc_type="api_doc", authority="draft")

    context = synthesizer.deterministic_fallback(RepairRagQuery(project="demo"), [], [approved, draft])

    assert [item.source.doc_id for item in context.hard_constraints] == ["doc:approved"]
    assert [item.source.doc_id for item in context.soft_hints] == ["doc:draft"]
    assert "diff --git" not in context.hard_constraints[0].text


def test_review_failed_skill_is_avoid_pattern_not_recommended_fix() -> None:
    synthesizer = ContextSynthesizer(RagContextSynthesizerConfig(enabled=False))
    failed = _candidate("failed", doc_type="skill", skill_type="review_failed")

    context = synthesizer.deterministic_fallback(RepairRagQuery(project="demo"), [failed], [])

    assert context.soft_hints == []
    assert context.avoid_patterns[0].use_type == "avoid_pattern"


def test_synthesizer_rejects_unknown_sources_and_does_not_persist_candidates() -> None:
    unknown = {
        "query_summary": "x",
        "hard_constraints": [
            {
                "text": "invented",
                "source": {"source": "local_doc", "source_type": "project_doc", "doc_id": "unknown", "chunk_id": "unknown"},
                "use_type": "business_constraint",
            }
        ],
    }
    llm = FakeLLM(json.dumps(unknown))
    synthesizer = ContextSynthesizer(RagContextSynthesizerConfig(), llm_client=llm)  # type: ignore[arg-type]

    context = synthesizer.synthesize(RepairRagQuery(project="demo"), [], [_candidate("approved", doc_type="api_doc", authority="approved")], RagStatus())

    assert context.hard_constraints == []
    assert llm.calls[0]["persist_call_record"] is False


def test_invalid_json_uses_deterministic_fallback() -> None:
    llm = FakeLLM("not json")
    status = RagStatus()
    synthesizer = ContextSynthesizer(RagContextSynthesizerConfig(), llm_client=llm)  # type: ignore[arg-type]

    context = synthesizer.synthesize(RepairRagQuery(project="demo"), [], [_candidate("approved", doc_type="api_doc", authority="approved")], status)

    assert context.hard_constraints
    assert "context_synthesizer" in status.degraded_stages
    assert len(llm.calls) == 2
