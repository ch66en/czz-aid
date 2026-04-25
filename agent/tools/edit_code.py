from __future__ import annotations

"""提供代码写入工具。"""

from pathlib import Path
from typing import Any

from agent.models import ToolCallResult, ToolSpec
from agent.tools.base import BaseTool


class EditCodeTool(BaseTool):
    """将指定内容写入目标文件。"""

    @property
    def spec(self) -> ToolSpec:
        """返回代码编辑工具的规格说明。"""
        return ToolSpec(name="edit_code", description="Write code to a file", requires_approval=True)

    def run(self, payload: dict[str, Any] | None = None) -> ToolCallResult:
        """根据路径和内容参数写入文件。"""
        data = payload or {}
        path = Path(str(data.get("path", "")))
        content = str(data.get("content", ""))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return ToolCallResult(success=True, output=str(path))
