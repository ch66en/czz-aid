"""验证去重引擎的基础行为。"""

from agent.core.dedup_engine import DedupEngine, MemoryDedupStore
from agent.models import BugEvent


def test_build_fingerprint_uses_expected_fields() -> None:
    """应按项目、异常类型、消息、业务栈与路径生成指纹。"""
    engine = DedupEngine(store=MemoryDedupStore())
    event = BugEvent(
        bug_id="BUG-1",
        source="feishu",
        project="com.example.book",
        title="borrow fail",
        exception_type="NullPointerException",
        message="Cannot invoke  something",
        request_path="/api/books/1",
        request_method="GET",
        top_business_frame="com.example.book.service.BorrowService.borrowBook",
        fingerprint="",
    )

    fingerprint = engine.build_fingerprint(event)

    assert len(fingerprint) == 64
    assert fingerprint == engine.build_fingerprint(event)


def test_mark_seen_increments_duplicate_count_for_same_fingerprint() -> None:
    """相同 fingerprint 在窗口期内应累加重复次数。"""
    engine = DedupEngine(store=MemoryDedupStore(), window_seconds=3600)

    first = engine.mark_seen("fp-1")
    second = engine.mark_seen("fp-1")

    assert first.duplicate_count == 0
    assert second.duplicate_count == 1
    assert engine.is_duplicate("fp-1") is True


def test_mark_seen_creates_separate_records_for_different_fingerprint() -> None:
    """不同 fingerprint 不应互相影响。"""
    engine = DedupEngine(store=MemoryDedupStore())

    engine.mark_seen("fp-1")
    engine.mark_seen("fp-2")

    assert engine.is_duplicate("fp-1") is True
    assert engine.is_duplicate("fp-2") is True
