from __future__ import annotations

"""提供按行定位 Java 符号工具。"""

from typing import Any

from agent.code_nav.ast_symbols import JavaAstSymbolExtractor
from agent.models import ToolResult, ToolSpec
from agent.tools.base import BaseTool, PermissionType


class ReadSymbolAtTool(BaseTool):
    """根据文件与行号定位最小符号并返回源码片段。"""

    def __init__(self, extractor: JavaAstSymbolExtractor | None = None) -> None:
        self.extractor = extractor or JavaAstSymbolExtractor()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="read_symbol_at",
            description="Read the smallest Java symbol enclosing a given source line.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or project-relative path to a Java source file."},
                    "line": {"type": "integer", "description": "1-based source line number."},
                },
                "required": ["path", "line"],
                "additionalProperties": False,
            },
            permission=PermissionType.READ_ONLY.value,
            executor="local",
        )

    @property
    def permission(self) -> PermissionType:
        return PermissionType.READ_ONLY

    def run(self, payload: dict[str, Any] | None = None) -> ToolResult:
        data = payload or {}
        path = str(data.get("path", ""))
        line = int(data.get("line", 0))
        try:
            result = self.extractor.find_symbol_at(path, line)
            return ToolResult(tool="read_symbol_at", success=True, exit_code=0, stdout_summary=f"symbol loaded at line {line}", data=result, artifacts=[])
        except Exception as exc:
            return ToolResult(tool="read_symbol_at", success=False, exit_code=1, stderr_summary=str(exc), data={"path": path, "line": line}, artifacts=[])
