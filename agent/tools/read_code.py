from __future__ import annotations

"""提供源码文件读取工具。"""

from pathlib import Path
from typing import Any

from agent.models import ToolResult, ToolSpec
from agent.tools.base import BaseTool, PermissionType


class ReadCodeTool(BaseTool):
    """读取指定源码文件并返回文本内容。"""

    @property
    def spec(self) -> ToolSpec:
        """返回源码读取工具的规格说明。"""
        return ToolSpec(name="read_code", description="Read a code file", input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}, permission=PermissionType.READ_ONLY.value, executor="local")

    @property
    def permission(self) -> PermissionType:
        """返回源码读取工具所需权限。"""
        return PermissionType.READ_ONLY

    def run(self, payload: dict[str, Any] | None = None) -> ToolResult:
        """根据路径参数读取源码文件。"""
        data = payload or {}
        path = Path(str(data.get("path", "")))
        if not path.exists():
            return ToolResult(tool="read_code", success=False, exit_code=1, stdout_summary="", stderr_summary="code file not found", data={"path": str(path)}, artifacts=[])
        return ToolResult(tool="read_code", success=True, exit_code=0, stdout_summary="code file loaded", stderr_summary="", data={"path": str(path), "content": path.read_text(encoding="utf-8")}, artifacts=[str(path)])
