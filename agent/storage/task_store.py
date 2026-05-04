from __future__ import annotations

"""提供修复任务的简单存储实现。"""

from datetime import datetime, timezone

from agent.models import RepairTask
from agent.storage.sqlite_repo import SQLiteRepo


class TaskStore:
    """以内存字典形式存取修复任务。"""

    def __init__(self) -> None:
        """初始化空的任务存储。"""
        self._items: dict[str, RepairTask] = {}

    def get(self, bug_id: str) -> RepairTask | None:
        """根据缺陷编号读取任务对象。"""
        return self._items.get(bug_id)

    def save(self, task: RepairTask) -> None:
        """保存或覆盖指定缺陷编号对应的任务。"""
        self._items[task.bug_id] = task


class SQLiteTaskStore:
    """基于 SQLite 的修复任务存储实现。"""

    def __init__(self, db_path: str) -> None:
        """初始化 SQLite 任务存储并确保表结构存在。"""
        self.repo = SQLiteRepo(db_path)
        self._initialize()

    def _initialize(self) -> None:
        """创建任务表。"""
        with self.repo.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    bug_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def get(self, bug_id: str) -> RepairTask | None:
        """根据缺陷编号读取任务对象。"""
        with self.repo.connect() as conn:
            row = conn.execute("SELECT payload FROM tasks WHERE bug_id = ?", (bug_id,)).fetchone()
        if row is None:
            return None
        return RepairTask.model_validate_json(row[0])

    def save(self, task: RepairTask) -> None:
        """保存或覆盖指定缺陷编号对应的任务。"""
        updated_at = datetime.now(timezone.utc).isoformat()
        with self.repo.connect() as conn:
            conn.execute(
                """
                INSERT INTO tasks (bug_id, status, payload, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(bug_id) DO UPDATE SET
                    status = excluded.status,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (task.bug_id, task.status.value, task.model_dump_json(), updated_at),
            )
