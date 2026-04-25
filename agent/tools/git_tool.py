from __future__ import annotations

"""提供最小化 Git 操作工具占位实现。"""

from typing import Any

from agent.models import ToolCallResult, ToolSpec
from agent.tools.base import BaseTool


class GitTool(BaseTool):
    """封装 Git 相关能力的最小工具骨架。"""

    @property
    def spec(self) -> ToolSpec:
        """返回 Git 工具的规格说明。"""
        return ToolSpec(name="git_tool", description="Minimal git helper", requires_approval=True)

    def run(self, payload: dict[str, Any] | None = None) -> ToolCallResult:
        """返回 Git 工具已就绪的占位结果。"""
        return ToolCallResult(success=True, output="git tool ready")
