"""验证入口聚合流水线。"""

from types import SimpleNamespace

from agent.config import AppConfig
from agent.core.permission_guard import PermissionGuard
from agent.core.repair_agent import RepairAgent
from agent.core.task_manager import TaskManager
from agent.core.tool_registry import ToolRegistry
from agent.ingestion.pipeline import IngestionPipeline
from agent.ingestion.sanitizer import Sanitizer
from agent.storage.session_store import SessionStore
from agent.storage.skill_store import SkillStore


class DummyRepairAgent:
    """模拟修复代理。"""

    def __init__(self) -> None:
        self.called = False

    def repair(self, bug_id: str) -> SimpleNamespace:
        self.called = True
        return SimpleNamespace(success=True, status="passed", message=f"repaired {bug_id}")


def test_pipeline_creates_bug_event_and_persists_it() -> None:
    """流水线应完成脱敏、解析、入库与触发修复。"""
    session_store = SessionStore()
    pipeline = IngestionPipeline(session_store=session_store, repair_agent=DummyRepairAgent(), sanitizer=Sanitizer())

    result = pipeline.process(
        raw_text="java.lang.NullPointerException: Cannot invoke x\n    at com.example.book.service.BorrowService.borrowBook(BorrowService.java:42)",
        bug_id="BUG-1",
        source="feishu",
        project="book-service",
        title="borrow failed",
        request_path="/api/books/1",
        request_method="POST",
        package_prefix="com.example",
    )

    assert result.bug_event.bug_id == "BUG-1"
    assert result.bug_event.top_business_frame.startswith("com.example")
    assert session_store.get("bug_event:BUG-1")["bug_id"] == "BUG-1"
    assert result.repair_result is not None
    assert result.repair_result.success is True
