from __future__ import annotations

import json
from pathlib import Path

from agent.config import AppConfig
from agent.models import BugEvent, StackFrame
from agent.rag.knowledge_service import KnowledgeService


def test_pre_retrieve_builds_structured_context_from_indexed_knowledge(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "skill-order-npe"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(
        "# Order NPE\n\n## 适用场景\nOrderService NullPointerException\n\n## 推荐排查步骤\nRead createOrder\n\n## 验证方式\nmvn test",
        encoding="utf-8",
    )
    skill_dir.joinpath("skill.meta.json").write_text(
        json.dumps(
            {
                "name": "skill-order-npe",
                "schema_version": 2,
                "skill_type": "review_passed",
                "use_types": ["recommended_fix", "validation_hint"],
                "project": "demo",
                "module": "order",
                "exception_type": "NullPointerException",
            }
        ),
        encoding="utf-8",
    )
    doc_path = tmp_path / "docs" / "api" / "order.md"
    doc_path.parent.mkdir(parents=True)
    doc_path.write_text(
        "---\nproject: demo\nmodule: order\nauthority: approved\n---\n# Order API\n\ncreateOrder must preserve order id.",
        encoding="utf-8",
    )
    config = AppConfig(
        workspace=str(tmp_path),
        project={"name": "demo"},
        rag={"db_path": str(tmp_path / "rag.db"), "retrieval": {"vector_min_score": -1.0}},
    )
    service = KnowledgeService(config=config)
    service.index_skills()
    service.index_local_docs()
    bug = BugEvent(
        bug_id="B",
        source="log",
        project="demo",
        title="",
        exception_type="NullPointerException",
        message="createOrder failed",
        frames=[StackFrame(file_path="OrderService.java", function_name="createOrder", line_number=12, module_name="order", is_business_code=True)],
        fingerprint="fp",
    )

    context, status = service.pre_retrieve_for_bug(bug, {})

    assert context.hard_constraints
    assert context.soft_hints
    assert context.validation_hints
    assert status.candidate_count >= 2
    assert status.selected_source_count >= 2
    assert "context_synthesizer" in status.degraded_stages


def test_total_rag_failure_returns_empty_context_and_failed_status(tmp_path: Path) -> None:
    config = AppConfig(project={"name": "demo"}, rag={"db_path": str(tmp_path / "rag.db")})
    service = KnowledgeService(config=config)

    class FailingHybrid:
        def retrieve_skills(self, query, status):
            raise RuntimeError("skill recall failed")

        def retrieve_project_docs(self, query, status):
            raise RuntimeError("doc recall failed")

    service.hybrid_retriever = FailingHybrid()  # type: ignore[assignment]
    bug = BugEvent(bug_id="B", source="log", project="demo", title="", exception_type="E", message="m", fingerprint="fp")

    context, status = service.pre_retrieve_for_bug(bug, {})

    assert context.selected_sources == []
    assert status.status == "failed"
    assert set(status.degraded_stages) >= {"skill_retrieval", "project_doc_retrieval"}
