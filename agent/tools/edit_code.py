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
        return ToolSpec(
            name="edit_code",
            description="Write code to a file or apply a unified diff patch",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
            permission=PermissionType.WORKSPACE_WRITE.value,
            executor="local",
        )

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
            patched, replacements = self._apply_simple_unified_diff(original, content)
            if replacements <= 0 or patched == original:
                return ToolResult(
                    tool="edit_code",
                    success=False,
                    exit_code=1,
                    stdout_summary=str(path),
                    stderr_summary="patch did not apply",
                    data={"path": str(path), "replacements": replacements},
                    artifacts=[],
                )
            path.write_text(patched, encoding="utf-8")
            return ToolResult(
                tool="edit_code",
                success=True,
                exit_code=0,
                stdout_summary=str(path),
                stderr_summary="",
                data={"path": str(path), "replacements": replacements},
                artifacts=[str(path)],
            )

        path.write_text(content, encoding="utf-8")
        return ToolResult(
            tool="edit_code",
            success=True,
            exit_code=0,
            stdout_summary=str(path),
            stderr_summary="",
            data={"path": str(path), "replacements": 1},
            artifacts=[str(path)],
        )

    def _looks_like_unified_diff(self, content: str) -> bool:
        text = content.lstrip()
        return text.startswith("diff --git") or text.startswith("--- ") or text.startswith("*** Begin Patch")

    def _apply_simple_unified_diff(self, original: str, diff_text: str) -> tuple[str, int]:
        """应用单文件 unified diff，支持多删少增的块替换；未命中时返回 0 次替换。"""
        hunks = self._parse_diff_hunks(diff_text)
        patched = original
        replacements = 0
        for removed, added in hunks:
            if not removed and not added:
                continue
            old_block = "\n".join(removed)
            new_block = "\n".join(added)
            if removed and old_block in patched:
                patched = patched.replace(old_block, new_block, 1)
                replacements += 1
                continue
            if removed and added:
                line_replacements = 0
                trial = patched
                for old_line, new_line in zip(removed, added):
                    if old_line in trial and old_line != new_line:
                        trial = trial.replace(old_line, new_line, 1)
                        line_replacements += 1
                if line_replacements > 0 and trial != patched:
                    patched = trial
                    replacements += line_replacements
                    continue
            if not removed and added:
                addition = new_block
                if addition and addition not in patched:
                    patched = f"{patched}\n{addition}" if patched else addition
                    replacements += 1
        return patched, replacements

    def _parse_diff_hunks(self, diff_text: str) -> list[tuple[list[str], list[str]]]:
        hunks: list[tuple[list[str], list[str]]] = []
        removed: list[str] = []
        added: list[str] = []
        in_hunk = False
        for line in diff_text.splitlines():
            if line.startswith("@@"):
                if in_hunk:
                    hunks.append((removed, added))
                removed = []
                added = []
                in_hunk = True
                continue
            if not in_hunk:
                continue
            if line.startswith("---") or line.startswith("+++"):
                continue
            if line.startswith("-"):
                removed.append(line[1:])
            elif line.startswith("+"):
                added.append(line[1:])
            elif line.startswith(" "):
                context = line[1:]
                removed.append(context)
                added.append(context)
        if in_hunk:
            hunks.append((removed, added))
        return hunks
