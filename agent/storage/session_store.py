from __future__ import annotations

"""提供会话级键值存储能力。"""

from typing import Any


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
