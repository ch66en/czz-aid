from __future__ import annotations

"""提供代码搜索工具。"""

from pathlib import Path
from typing import Any

from agent.models import ToolCallResult, ToolSpec
from agent.tools.base import BaseTool


class SearchCodeTool(BaseTool):
    """在指定目录下搜索包含关键字的 Python 文件。"""

    @property
    def spec(self) -> ToolSpec:
        """返回代码搜索工具的规格说明。"""
        return ToolSpec(name="search_code", description="Search text in code files")

    def run(self, payload: dict[str, Any] | None = None) -> ToolCallResult:
        """遍历目录并收集包含目标关键字的文件路径。"""
        data = payload or {}
        root = Path(str(data.get("root", ".")))
        keyword = str(data.get("keyword", ""))
        matches: list[str] = []
        for path in root.rglob("*.py"):
            try:
                # 文件可能被占用或不可读，因此这里容忍单文件失败。
                if keyword and keyword in path.read_text(encoding="utf-8"):
                    matches.append(str(path))
            except OSError:
                continue
        return ToolCallResult(success=True, output="\n".join(matches))
