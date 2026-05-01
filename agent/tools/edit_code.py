from __future__ import annotations

"""Patch application tool."""

from pathlib import Path
import re
from typing import Any

from agent.config import AppConfig
from agent.models import ToolResult, ToolSpec
from agent.tools.base import BaseTool, PermissionType


class EditCodeTool(BaseTool):
    """Apply a single-file unified diff to a target file."""

    def __init__(self, config: AppConfig | None = None) -> None:
        self.config = config

    @property
    def spec(self) -> ToolSpec:
        """Return the edit tool metadata."""
        return ToolSpec(
            name="edit_code",
            description=(
                "Apply a single-file unified diff to the target file. "
                "The content must start with diff headers such as '--- a/...' and '+++ b/...'. "
                "Raw snippets, method bodies, and full-file rewrites are rejected."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or project-relative path of the file to patch."},
                    "content": {"type": "string", "description": "Unified diff text for exactly one file; raw replacement text is not accepted."},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            permission=PermissionType.WORKSPACE_WRITE.value,
            executor="local",
        )

    @property
    def permission(self) -> PermissionType:
        """Return the permission required by this tool."""
        return PermissionType.WORKSPACE_WRITE

    def run(self, payload: dict[str, Any] | None = None) -> ToolResult:
        """Apply a unified diff to the requested path."""
        data = payload or {}
        path = self._resolve_path(str(data.get("path", "")))
        content = str(data.get("content", ""))

        if not self._looks_like_unified_diff(content):
            return ToolResult(
                tool="edit_code",
                success=False,
                exit_code=1,
                stdout_summary=str(path),
                stderr_summary="edit_code requires a unified diff; raw snippets and full-file content are rejected",
                data={"path": str(path)},
                artifacts=[str(path)],
            )

        path.parent.mkdir(parents=True, exist_ok=True)
        original = path.read_text(encoding="utf-8") if path.exists() else ""
        file_existed = path.exists()
        try:
            patched = self._apply_simple_unified_diff(original, content)
        except ValueError as exc:
            return ToolResult(tool="edit_code", success=False, exit_code=1, stdout_summary=str(path), stderr_summary=str(exc), data={"path": str(path)}, artifacts=[str(path)])
        if patched == original:
            return ToolResult(tool="edit_code", success=False, exit_code=1, stdout_summary=str(path), stderr_summary="patch did not change file", data={"path": str(path)}, artifacts=[str(path)])
        path.write_text(patched, encoding="utf-8")

        return ToolResult(
            tool="edit_code", success=True, exit_code=0, stdout_summary=str(path), stderr_summary="",
            data={"path": str(path), "original_content": original, "file_existed": file_existed},
            artifacts=[str(path)],
        )

    def _looks_like_unified_diff(self, content: str) -> bool:
        text = content.lstrip()
        return text.startswith("diff --git") or text.startswith("--- ")

    def _apply_simple_unified_diff(self, original: str, diff_text: str) -> str:
        """Apply a single-file unified diff with real hunk semantics."""
        hunks = self._parse_unified_diff_hunks(diff_text)
        if not hunks:
            raise ValueError("unsupported or empty unified diff")

        newline = "\r\n" if "\r\n" in original and original.count("\r\n") >= original.count("\n") else "\n"
        had_trailing_newline = original.endswith(("\n", "\r"))
        original_lines = original.splitlines()
        patched_lines: list[str] = []
        cursor = 0
        line_offset = 0

        for hunk in hunks:
            old_block = [text for op, text in hunk["lines"] if op != "+"]
            new_block = [text for op, text in hunk["lines"] if op != "-"]
            if not old_block and not new_block:
                continue

            index = self._find_hunk_index(original_lines, old_block, cursor)
            if index is None:
                index = self._header_fallback_index(hunk, original_lines, old_block, line_offset)
            if index is None:
                needle = next((text for op, text in hunk["lines"] if op in {" ", "-"}), "")
                raise ValueError(f"patch context not found: {needle}")

            patched_lines.extend(original_lines[cursor:index])
            patched_lines.extend(new_block)
            cursor = index + len(old_block)
            line_offset += len(new_block) - len(old_block)

        patched_lines.extend(original_lines[cursor:])
        patched = newline.join(patched_lines)
        if had_trailing_newline:
            patched += newline
        return patched

    def _parse_unified_diff_hunks(self, diff_text: str) -> list[dict[str, Any]]:
        hunks: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for line in diff_text.splitlines():
            if line.startswith("@@"):
                match = re.match(r"@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@", line)
                if match is None:
                    raise ValueError(f"unsupported hunk header: {line}")
                current = {
                    "old_start": int(match.group("old_start")),
                    "old_count": int(match.group("old_count") or "1"),
                    "new_start": int(match.group("new_start")),
                    "new_count": int(match.group("new_count") or "1"),
                    "lines": [],
                }
                hunks.append(current)
                continue
            if current is None:
                continue
            if line.startswith("\\"):
                continue
            if line.startswith((" ", "+", "-")):
                if line.startswith(("+++", "---")):
                    continue
                current["lines"].append((line[:1], line[1:]))
                continue
            raise ValueError(f"unsupported hunk line: {line}")
        return hunks

    def _find_hunk_index(self, original_lines: list[str], old_block: list[str], start: int) -> int | None:
        if not old_block:
            return None
        last_start = len(original_lines) - len(old_block)
        for index in range(max(start, 0), last_start + 1):
            if original_lines[index : index + len(old_block)] == old_block:
                return index
        return None

    def _header_fallback_index(self, hunk: dict[str, Any], original_lines: list[str], old_block: list[str], line_offset: int) -> int | None:
        old_start = int(hunk["old_start"])
        old_count = int(hunk["old_count"])
        index = (old_start if old_count == 0 else old_start - 1) + line_offset
        if index < 0 or index > len(original_lines):
            return None
        if old_block and original_lines[index : index + len(old_block)] != old_block:
            return None
        return index

    def _resolve_path(self, raw_path: str) -> Path:
        path = Path(raw_path).expanduser()
        if path.is_absolute() or self.config is None:
            return path
        project_root = Path(self.config.project.root).expanduser()
        return project_root / path
