from __future__ import annotations

"""提供代码写入工具。"""

from pathlib import Path
from typing import Any

from agent.models import ToolResult, ToolSpec
from agent.tools.base import BaseTool, PermissionType


class EditCodeTool(BaseTool):
    """将指定内容写入目标文件。"""

    @property
    def spec(self) -> ToolSpec:
        """返回代码编辑工具的规格说明。"""
        return ToolSpec(name="edit_code", description="Write code to a file", input_schema={"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}, permission=PermissionType.WORKSPACE_WRITE.value, executor="local")

    @property
    def permission(self) -> PermissionType:
        """返回代码编辑工具所需权限。"""
        return PermissionType.WORKSPACE_WRITE

    def run(self, payload: dict[str, Any] | None = None) -> ToolResult:
        """根据路径和内容参数写入文件。"""
        data = payload or {}
        path = Path(str(data.get("path", "")))
        content = str(data.get("content", ""))
        path.parent.mkdir(parents=True, exist_ok=True)

        if self._looks_like_unified_diff(content):
            original = path.read_text(encoding="utf-8") if path.exists() else ""
            patched = self._apply_simple_unified_diff(original, content)
            path.write_text(patched, encoding="utf-8")
        else:
            path.write_text(content, encoding="utf-8")

        return ToolResult(tool="edit_code", success=True, exit_code=0, stdout_summary=str(path), stderr_summary="", data={"path": str(path)}, artifacts=[str(path)])

    def _looks_like_unified_diff(self, content: str) -> bool:
        text = content.lstrip()
        return text.startswith("diff --git") or text.startswith("--- ") or text.startswith("*** Begin Patch")

    def _apply_simple_unified_diff(self, original: str, diff_text: str) -> str:
        """应用单文件的简单 unified diff；不支持复杂多文件/多 hunk 场景。"""
        lines = diff_text.splitlines()
        removed: list[str] = []
        added: list[str] = []
        in_hunk = False
        for line in lines:
            if line.startswith("@@"):
                in_hunk = True
                continue
            if not in_hunk:
                continue
            if line.startswith("+") and not line.startswith("+++"):
                added.append(line[1:])
            elif line.startswith("-") and not line.startswith("---"):
                removed.append(line[1:])

        if removed and removed[0] == "pass" and added:
            return original.replace("pass", "\n".join(added), 1)
        if removed and added and len(removed) == len(added):
            patched = original
            for old, new in zip(removed, added):
                patched = patched.replace(old, new, 1)
            return patched
        return original
