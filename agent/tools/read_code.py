from __future__ import annotations

"""提供源码文件读取工具。"""

from pathlib import Path
from typing import Any

from agent.models import ToolCallResult, ToolSpec
from agent.tools.base import BaseTool


class ReadCodeTool(BaseTool):
    """读取指定源码文件并返回文本内容。"""

    @property
    def spec(self) -> ToolSpec:
        """返回源码读取工具的规格说明。"""
        return ToolSpec(name="read_code", description="Read a code file")

    def run(self, payload: dict[str, Any] | None = None) -> ToolCallResult:
        """根据路径参数读取源码文件。"""
        data = payload or {}
        path = Path(str(data.get("path", "")))
        if not path.exists():
            return ToolCallResult(success=False, output="code file not found")
        return ToolCallResult(success=True, output=path.read_text(encoding="utf-8"))
