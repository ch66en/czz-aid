from __future__ import annotations

"""提供最小化 Gitee 操作工具占位实现。"""

from typing import Any

from agent.models import ToolResult, ToolSpec
from agent.tools.base import BaseTool


class GiteeTool(BaseTool):
    """封装 Gitee 相关能力的最小工具骨架。"""

    @property
    def spec(self) -> ToolSpec:
        """返回 Gitee 工具的规格说明。"""
        return ToolSpec(name="gitee_tool", description="Minimal gitee helper", requires_approval=True)

    def run(self, payload: dict[str, Any] | None = None) -> ToolResult:
        """返回 Gitee 工具已就绪的占位结果。"""
        return ToolResult(
            tool="gitee_tool",
            success=True,
            exit_code=0,
            stdout_summary="gitee tool ready",
            stderr_summary="",
            data={},
            artifacts=[],
        )
