from __future__ import annotations

"""提供日志文件读取工具。"""

from pathlib import Path
from typing import Any

from agent.models import ToolCallResult, ToolSpec
from agent.tools.base import BaseTool


class ReadLogTool(BaseTool):
    """读取指定日志文件并返回其文本内容。"""

    @property
    def spec(self) -> ToolSpec:
        """返回日志读取工具的规格说明。"""
        return ToolSpec(name="read_log", description="Read a log file")

    def run(self, payload: dict[str, Any] | None = None) -> ToolCallResult:
        """根据路径参数读取日志文件。"""
        data = payload or {}
        path = Path(str(data.get("path", "")))
        if not path.exists():
            return ToolCallResult(success=False, output="log file not found")
        return ToolCallResult(success=True, output=path.read_text(encoding="utf-8"))
