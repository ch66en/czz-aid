from __future__ import annotations

"""管理可注册工具的元数据与实例。"""

from agent.models import ToolSpec
from agent.tools.base import BaseTool


class ToolRegistry:
    """负责维护工具名称到工具实例的映射关系。"""

    def __init__(self) -> None:
        """初始化空的工具注册表。"""
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """注册一个工具实例到注册表。"""
        self._tools[tool.spec.name] = tool

    def get(self, name: str) -> BaseTool | None:
        """根据工具名称获取已注册的工具实例。"""
        return self._tools.get(name)

    def list_specs(self) -> list[ToolSpec]:
        """返回当前所有已注册工具的描述信息。"""
        return [tool.spec for tool in self._tools.values()]
