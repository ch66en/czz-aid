from __future__ import annotations

"""提供 Java AST 符号导航工具。"""

from typing import Any

from agent.code_nav.ast_symbols import JavaAstSymbolExtractor
from agent.models import ToolResult, ToolSpec
from agent.tools.base import BaseTool, PermissionType


class AstSymbolsTool(BaseTool):
    """提取 Java 文件符号信息。"""

    def __init__(self, extractor: JavaAstSymbolExtractor | None = None) -> None:
        self.extractor = extractor or JavaAstSymbolExtractor()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="ast_symbols",
            description="Extract Java AST symbols",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
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
        try:
            result = self.extractor.extract(path)
            return ToolResult(tool="ast_symbols", success=True, exit_code=0, stdout_summary=f"found {len(result.get('symbols', []))} symbols", data=result, artifacts=[])
        except Exception as exc:
            return ToolResult(tool="ast_symbols", success=False, exit_code=1, stderr_summary=str(exc), data={"path": path}, artifacts=[])
