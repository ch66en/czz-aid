from __future__ import annotations

"""提供技能内容的简单存储能力。"""


class SkillStore:
    """以内存字典形式保存技能文本。"""

    def __init__(self) -> None:
        """初始化空的技能存储。"""
        self._items: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        """根据键读取技能文本。"""
        return self._items.get(key)

    def put(self, key: str, value: str) -> None:
        """写入或覆盖指定键的技能文本。"""
        self._items[key] = value
