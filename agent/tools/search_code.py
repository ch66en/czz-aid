from __future__ import annotations

"""提供代码搜索工具。"""

from pathlib import Path
from typing import Any

from agent.models import ToolResult, ToolSpec
from agent.tools.base import BaseTool, PermissionType


class SearchCodeTool(BaseTool):
    """在指定目录下搜索包含关键字的 Python 文件。"""

    @property
    def spec(self) -> ToolSpec:
        """返回代码搜索工具的规格说明。"""
        return ToolSpec(name="search_code", description="Search text in code files", input_schema={"type": "object", "properties": {"root": {"type": "string"}, "keyword": {"type": "string"}}, "required": ["keyword"]}, permission=PermissionType.READ_ONLY.value, executor="local")

    @property
    def permission(self) -> PermissionType:
        """返回代码搜索工具所需权限。"""
        return PermissionType.READ_ONLY

    def run(self, payload: dict[str, Any] | None = None) -> ToolResult:
        """遍历目录并收集包含目标关键字的文件路径。"""
        data = payload or {}
        root = Path(str(data.get("root", ".")))
        keyword = str(data.get("keyword", ""))
        matches: list[str] = []
        for path in root.rglob("*.py"):
            try:
                if keyword and keyword in path.read_text(encoding="utf-8"):
                    matches.append(str(path))
            except OSError:
                continue
        return ToolResult(tool="search_code", success=True, exit_code=0, stdout_summary=f"found {len(matches)} file(s)", stderr_summary="", data={"keyword": keyword, "matches": matches}, artifacts=matches)
