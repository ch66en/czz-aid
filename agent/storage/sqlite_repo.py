from __future__ import annotations

"""封装 SQLite 数据库连接初始化逻辑。"""

from pathlib import Path
import sqlite3


class SQLiteRepo:
    """负责按需创建数据库目录并建立连接。"""

    def __init__(self, db_path: str) -> None:
        """初始化 SQLite 数据库文件路径。"""
        self.db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:
        """确保目录存在后返回 SQLite 连接对象。"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.db_path)
