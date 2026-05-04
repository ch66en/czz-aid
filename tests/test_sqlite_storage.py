"""验证 SQLite 存储后端的持久化行为。"""

from pathlib import Path

from agent.core.dedup_engine import DedupEngine, SQLiteDedupStore
from agent.models import RepairTask, TaskStatus
from agent.storage.session_store import SQLiteSessionStore
from agent.storage.task_store import SQLiteTaskStore


def test_sqlite_session_store_survives_new_instance(tmp_path: Path) -> None:
    """会话数据应在重新创建 store 后仍可读取。"""
    db_path = tmp_path / "agent.db"
    first = SQLiteSessionStore(str(db_path))
    first.put("bug_event:BUG-1", {"bug_id": "BUG-1", "message": "boom"})

    second = SQLiteSessionStore(str(db_path))

    assert second.get("bug_event:BUG-1") == {"bug_id": "BUG-1", "message": "boom"}
    assert second.list_keys("bug_event:") == ["bug_event:BUG-1"]


def test_sqlite_task_store_survives_new_instance(tmp_path: Path) -> None:
    """任务状态应在重新创建 store 后仍可读取。"""
    db_path = tmp_path / "agent.db"
    first = SQLiteTaskStore(str(db_path))
    task = RepairTask(bug_id="BUG-2", project="demo", status=TaskStatus.REVIEWING, pr_url="https://gitee.com/demo/pr/1")
    first.save(task)

    second = SQLiteTaskStore(str(db_path))
    restored = second.get("BUG-2")

    assert restored is not None
    assert restored.bug_id == "BUG-2"
    assert restored.status == TaskStatus.REVIEWING
    assert restored.pr_url == "https://gitee.com/demo/pr/1"


def test_sqlite_dedup_store_survives_new_instance(tmp_path: Path) -> None:
    """去重窗口应在重新创建 store 后仍可判断重复。"""
    db_path = tmp_path / "agent.db"
    first = DedupEngine(store=SQLiteDedupStore(str(db_path)))
    first.mark_seen("fp-1")

    second = DedupEngine(store=SQLiteDedupStore(str(db_path)))

    assert second.is_duplicate("fp-1") is True
