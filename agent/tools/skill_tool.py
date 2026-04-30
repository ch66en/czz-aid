from __future__ import annotations

"""提供技能数据读写工具。"""

from typing import Any

from agent.models import ToolResult, ToolSpec
from agent.storage.skill_store import SkillStore
from agent.tools.base import BaseTool, PermissionType


class SkillTool(BaseTool):
    """基于技能存储提供统一读写接口。"""

    def __init__(self, skill_store: SkillStore) -> None:
        """初始化技能工具所依赖的存储对象。"""
        self.skill_store = skill_store

    @property
    def spec(self) -> ToolSpec:
        """返回技能工具的规格说明。"""
        return ToolSpec(
            name="skill_tool",
            description="Read or write reusable repair skill data by key.",
            input_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Skill key to read or write."},
                    "value": {"type": "string", "description": "Optional skill body to write. Omit to read."},
                },
                "required": ["key"],
                "additionalProperties": False,
            },
            permission=PermissionType.READ_ONLY.value,
            executor="local",
        )

    @property
    def permission(self) -> PermissionType:
        return PermissionType.READ_ONLY

    def run(self, payload: dict[str, Any] | None = None) -> ToolResult:
        """根据是否提供值决定读取或写入技能数据。"""
        data = payload or {}
        key = str(data.get("key", ""))
        value = data.get("value")
        if value is None:
            return ToolResult(tool="skill_tool", success=True, exit_code=0, stdout_summary=str(self.skill_store.get(key)), data={"key": key}, artifacts=[])
        self.skill_store.put(key, str(value))
        return ToolResult(tool="skill_tool", success=True, exit_code=0, stdout_summary=key, data={"key": key}, artifacts=[])
