"""验证入口聚合流水线。"""

from pathlib import Path
from types import SimpleNamespace

from agent.config import AppConfig
from agent.ingestion.pipeline import IngestionPipeline
from agent.ingestion.sanitizer import Sanitizer
from agent.storage.session_store import SessionStore


class DummyRepairAgent:
    def __init__(self) -> None:
        self.called = False

    def repair(self, bug_id: str) -> SimpleNamespace:
        self.called = True
        return SimpleNamespace(success=True, status="passed", message=f"repaired {bug_id}")


def _write_fixture(tmp_path: Path) -> Path:
    file_path = tmp_path / "BookService.java"
    file_path.write_text(
        "package com.demo.book;\n\npublic class BookService {\n    private final BookRepository bookRepository;\n\n    public BookService(BookRepository bookRepository) {\n        this.bookRepository = bookRepository;\n    }\n\n    public String getTitle(Long id) {\n        Book book = bookRepository.findById(id);\n        return book.getTitle();\n    }\n\n    public Book detail(Long id) {\n        return bookRepository.findById(id);\n    }\n}\n",
        encoding="utf-8",
    )
    return file_path


def test_pipeline_creates_bug_event_and_persists_it(tmp_path) -> None:
    session_store = SessionStore()
    fixture = _write_fixture(tmp_path)
    pipeline = IngestionPipeline(session_store=session_store, repair_agent=DummyRepairAgent(), sanitizer=Sanitizer())

    result = pipeline.process(
        raw_text="java.lang.NullPointerException: Cannot invoke x\n    at com.demo.book.BookService.getTitle(BookService.java:42)\n    at org.springframework.web.method.support.InvocableHandlerMethod.doInvoke(InvocableHandlerMethod.java:255)\n    at java.base/jdk.internal.reflect.NativeMethodAccessorImpl.invoke0(Native Method)",
        bug_id="BUG-1",
        source="feishu",
        project="book-service",
        title="borrow failed",
        request_path="/api/books/1",
        request_method="POST",
        package_prefix="com.demo",
    )

    assert result.bug_event.bug_id == "BUG-1"
    assert result.bug_event.traceback
    assert len(result.bug_event.frames) > 0
    assert result.bug_event.frames[0].file_path == "BookService.java"
    assert result.bug_event.frames[0].function_name == "getTitle"
    assert result.bug_event.frames[0].line_number == 42
    assert result.bug_event.frames[0].module_name == "com.demo.book.BookService"
    saved = session_store.get("bug_event:BUG-1")
    assert saved["bug_id"] == "BUG-1"
    assert "frames" in saved
    assert len(saved["frames"]) > 0
    restored = result.bug_event.model_validate(saved)
    assert restored.frames[0].function_name == "getTitle"
    assert "frame_contexts" in saved or result.session_snapshot.get("frame_contexts") is not None
    assert result.repair_result is not None
    assert result.repair_result.success is True
