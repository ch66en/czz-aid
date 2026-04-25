from __future__ import annotations

"""提供最小化飞书操作工具占位实现。"""

from typing import Any

from agent.models import ToolResult, ToolSpec
from agent.tools.base import BaseTool


class FeishuTool(BaseTool):
    """封装飞书相关能力的最小工具骨架。"""

    @property
    def spec(self) -> ToolSpec:
        """返回飞书工具的规格说明。"""
        return ToolSpec(name="feishu_tool", description="Minimal feishu helper", requires_approval=True)

    def run(self, payload: dict[str, Any] | None = None) -> ToolResult:
        """返回飞书工具已就绪的占位结果。"""
        return ToolResult(
            tool="feishu_tool",
            success=True,
            exit_code=0,
            stdout_summary="feishu tool ready",
            stderr_summary="",
            data={},
            artifacts=[],
        )
