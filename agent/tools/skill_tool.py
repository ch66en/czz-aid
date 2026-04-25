from __future__ import annotations

"""提供技能数据读写工具。"""

from typing import Any

from agent.models import ToolCallResult, ToolSpec
from agent.storage.skill_store import SkillStore
from agent.tools.base import BaseTool


class SkillTool(BaseTool):
    """基于技能存储提供统一读写接口。"""

    def __init__(self, skill_store: SkillStore) -> None:
        """初始化技能工具所依赖的存储对象。"""
        self.skill_store = skill_store

    @property
    def spec(self) -> ToolSpec:
        """返回技能工具的规格说明。"""
        return ToolSpec(name="skill_tool", description="Read and write skill data")

    def run(self, payload: dict[str, Any] | None = None) -> ToolCallResult:
        """根据是否提供值决定读取或写入技能数据。"""
        data = payload or {}
        key = str(data.get("key", ""))
        value = data.get("value")
        if value is None:
            return ToolCallResult(success=True, output=str(self.skill_store.get(key)))
        self.skill_store.put(key, str(value))
        return ToolCallResult(success=True, output=key)
