from __future__ import annotations

"""提供缺陷事件去重与重复计数能力。"""

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from agent.models import BugEvent


@dataclass(slots=True)
class DedupRecord:
    """表示一次 fingerprint 的去重记录。"""

    fingerprint: str
    first_seen_at: datetime
    last_seen_at: datetime
    duplicate_count: int = 0


class DedupStore(Protocol):
    """定义去重存储的可替换接口。"""

    def get(self, fingerprint: str) -> DedupRecord | None:
        """读取指定 fingerprint 的记录。"""
        ...

    def upsert(self, record: DedupRecord) -> None:
        """保存或更新指定记录。"""
        ...


class MemoryDedupStore:
    """基于内存字典的去重存储实现。"""

    def __init__(self) -> None:
        """初始化空存储。"""
        self._items: dict[str, DedupRecord] = {}

    def get(self, fingerprint: str) -> DedupRecord | None:
        """读取记录。"""
        return self._items.get(fingerprint)

    def upsert(self, record: DedupRecord) -> None:
        """保存记录。"""
        self._items[record.fingerprint] = record


class SQLiteDedupStore:
    """基于 SQLite 的去重存储实现。"""

    def __init__(self, db_path: str) -> None:
        """初始化 SQLite 存储。"""
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        """创建数据库连接。"""
        return sqlite3.connect(self.db_path)

    def _initialize(self) -> None:
        """初始化表结构。"""
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dedup_records (
                    fingerprint TEXT PRIMARY KEY,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    duplicate_count INTEGER NOT NULL
                )
                """
            )

    def get(self, fingerprint: str) -> DedupRecord | None:
        """读取 fingerprint 记录。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT fingerprint, first_seen_at, last_seen_at, duplicate_count FROM dedup_records WHERE fingerprint = ?",
                (fingerprint,),
            ).fetchone()
        if row is None:
            return None
        return DedupRecord(
            fingerprint=row[0],
            first_seen_at=datetime.fromisoformat(row[1]),
            last_seen_at=datetime.fromisoformat(row[2]),
            duplicate_count=int(row[3]),
        )

    def upsert(self, record: DedupRecord) -> None:
        """保存或更新记录。"""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO dedup_records (fingerprint, first_seen_at, last_seen_at, duplicate_count)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(fingerprint) DO UPDATE SET
                    first_seen_at = excluded.first_seen_at,
                    last_seen_at = excluded.last_seen_at,
                    duplicate_count = excluded.duplicate_count
                """,
                (
                    record.fingerprint,
                    record.first_seen_at.isoformat(),
                    record.last_seen_at.isoformat(),
                    record.duplicate_count,
                ),
            )


class DedupEngine:
    """基于 fingerprint 规则判断缺陷是否重复。"""

    def __init__(self, store: DedupStore | None = None, window_seconds: int = 3600) -> None:
        """初始化去重引擎。"""
        self.store = store or MemoryDedupStore()
        self.window = timedelta(seconds=window_seconds)

    def build_fingerprint(self, bug_event: BugEvent) -> str:
        """按约定字段构建缺陷指纹。"""
        normalized_message = self._normalize_message(bug_event.message)
        raw = f"{bug_event.project}{bug_event.exception_type}{normalized_message}{bug_event.top_business_frame}{bug_event.request_path}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def is_duplicate(self, fingerprint: str) -> bool:
        """判断 fingerprint 在窗口期内是否重复出现。"""
        record = self.store.get(fingerprint)
        if record is None:
            return False
        now = datetime.now(timezone.utc)
        return now - record.last_seen_at <= self.window

    def mark_seen(self, fingerprint: str) -> DedupRecord:
        """记录 fingerprint 的出现并更新重复计数。"""
        now = datetime.now(timezone.utc)
        record = self.store.get(fingerprint)
        if record is None:
            record = DedupRecord(fingerprint=fingerprint, first_seen_at=now, last_seen_at=now, duplicate_count=0)
            self.store.upsert(record)
            return DedupRecord(
                fingerprint=record.fingerprint,
                first_seen_at=record.first_seen_at,
                last_seen_at=record.last_seen_at,
                duplicate_count=record.duplicate_count,
            )

        if now - record.last_seen_at <= self.window:
            record.duplicate_count += 1
        else:
            record.first_seen_at = now
            record.duplicate_count = 0
        record.last_seen_at = now
        self.store.upsert(record)
        return DedupRecord(
            fingerprint=record.fingerprint,
            first_seen_at=record.first_seen_at,
            last_seen_at=record.last_seen_at,
            duplicate_count=record.duplicate_count,
        )

    def _normalize_message(self, message: str) -> str:
        """对异常消息进行轻量归一化。"""
        return " ".join(message.split()).strip().lower()
