from __future__ import annotations

"""提供 Git diff 工具。"""

from typing import Any

from agent.models import ToolResult, ToolSpec
from agent.tools.base import BaseTool, PermissionType


class GitDiffTool(BaseTool):
    """返回当前仓库的差异摘要。"""

    @property
    def spec(self) -> ToolSpec:
        """返回 Git diff 工具的规格说明。"""
        return ToolSpec(
            name="git_diff",
            description="Show git diff",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            permission=PermissionType.VCS_WRITE,
            executor="local",
        )

    @property
    def permission(self) -> PermissionType:
        """返回 Git diff 工具所需权限。"""
        return PermissionType.VCS_WRITE

    def run(self, payload: dict[str, Any] | None = None) -> ToolResult:
        """返回占位差异结果，后续可替换为真实 git diff 执行。"""
        return ToolResult(tool="git_diff", success=True, exit_code=0, stdout_summary="git diff ready", stderr_summary="", data={}, artifacts=[])
