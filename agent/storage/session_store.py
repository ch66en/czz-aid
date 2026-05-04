from __future__ import annotations

"""提供会话级键值存储能力。"""

import json
from datetime import datetime, timezone
from typing import Any

from agent.storage.sqlite_repo import SQLiteRepo


class SessionStore:
    """以内存字典形式保存会话数据。"""

    def __init__(self) -> None:
        """初始化空的会话存储。"""
        self._items: dict[str, Any] = {}

    def get(self, key: str) -> Any:
        """根据键读取会话数据。"""
        return self._items.get(key)

    def put(self, key: str, value: Any) -> None:
        """写入或覆盖指定键的会话数据。"""
        self._items[key] = value

    def delete(self, key: str) -> None:
        """删除指定键的会话数据。"""
        self._items.pop(key, None)

    def list_keys(self, prefix: str = "") -> list[str]:
        """列出指定前缀下的会话键。"""
        return sorted(key for key in self._items if key.startswith(prefix))


class SQLiteSessionStore:
    """基于 SQLite 的会话键值存储实现。"""

    def __init__(self, db_path: str) -> None:
        """初始化 SQLite 会话存储并确保表结构存在。"""
        self.repo = SQLiteRepo(db_path)
        self._initialize()

    def _initialize(self) -> None:
        """创建会话表。"""
        with self.repo.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def get(self, key: str) -> Any:
        """根据键读取会话数据。"""
        with self.repo.connect() as conn:
            row = conn.execute("SELECT value FROM sessions WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def put(self, key: str, value: Any) -> None:
        """写入或覆盖指定键的会话数据。"""
        payload = json.dumps(value, ensure_ascii=False, default=str)
        updated_at = datetime.now(timezone.utc).isoformat()
        with self.repo.connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, payload, updated_at),
            )

    def delete(self, key: str) -> None:
        """删除指定键的会话数据。"""
        with self.repo.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE key = ?", (key,))

    def list_keys(self, prefix: str = "") -> list[str]:
        """列出指定前缀下的会话键。"""
        with self.repo.connect() as conn:
            rows = conn.execute("SELECT key FROM sessions WHERE key LIKE ? ORDER BY key", (f"{prefix}%",)).fetchall()
        return [str(row[0]) for row in rows]
