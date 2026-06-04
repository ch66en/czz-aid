from __future__ import annotations

"""Patch application tool."""

from pathlib import Path
import re
import shlex
import subprocess
from typing import Any

from agent.config import AppConfig
from agent.models import ToolResult, ToolSpec
from agent.tools.base import BaseTool, PermissionType


class EditCodeTool(BaseTool):
    """Apply a single-file unified diff to a target file."""

    TOOL_NAME = "edit_code"
    MAX_HUNKS = 3
    MAX_ADDED_LINES = 50
    MAX_DELETED_LINES = 30
    EDITABLE_SOURCE_ROOTS = (("src", "main", "java"), ("src", "test", "java"))
    FORBIDDEN_DIRS = {".git", ".github", ".gitee"}
    FORBIDDEN_NAMES = {
        ".env",
        "dockerfile",
        "jenkinsfile",
        "pom.xml",
        "build.gradle",
        "gradle.properties",
    }
    FORBIDDEN_SUFFIXES = {".yaml", ".yml", ".properties"}

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
        tool_name = self.spec.name
        data = payload or {}
        path = self._resolve_path(str(data.get("path", "")))
        content = str(data.get("content", ""))

        if not self._looks_like_unified_diff(content):
            return ToolResult(
                tool=tool_name,
                success=False,
                exit_code=1,
                stdout_summary=str(path),
                stderr_summary=f"{tool_name} requires a unified diff; raw snippets and full-file content are rejected",
                data={"path": str(path)},
                artifacts=[str(path)],
            )

        validation_error = self._validate_patch_request(path, content)
        if validation_error:
            return ToolResult(
                tool=tool_name,
                success=False,
                exit_code=1,
                stdout_summary=str(path),
                stderr_summary=validation_error,
                data={"path": str(path)},
                artifacts=[str(path)],
            )

        try:
            hunks = self._parse_unified_diff_hunks(content)
            self._validate_patch_size(hunks)
        except ValueError as exc:
            return ToolResult(tool=tool_name, success=False, exit_code=1, stdout_summary=str(path), stderr_summary=str(exc), data={"path": str(path)}, artifacts=[str(path)])

        path.parent.mkdir(parents=True, exist_ok=True)
        original = path.read_text(encoding="utf-8") if path.exists() else ""
        file_existed = path.exists()
        try:
            patched = self._apply_simple_unified_diff(original, content, hunks)
        except ValueError as exc:
            return ToolResult(tool=tool_name, success=False, exit_code=1, stdout_summary=str(path), stderr_summary=str(exc), data={"path": str(path)}, artifacts=[str(path)])
        if patched == original:
            return ToolResult(tool=tool_name, success=False, exit_code=1, stdout_summary=str(path), stderr_summary="patch did not change file", data={"path": str(path)}, artifacts=[str(path)])
        path.write_text(patched, encoding="utf-8")

        lint_result = self._run_lint(path)
        if lint_result is not None and not lint_result["passed"]:
            path.write_text(original, encoding="utf-8")
            return ToolResult(
                tool=tool_name, success=False, exit_code=1,
                stdout_summary=str(path),
                stderr_summary=f"lint failed:\n{lint_result['output']}",
                data={"path": str(path), "original_content": original, "file_existed": file_existed, "lint_passed": False, "lint_output": lint_result["output"]},
                artifacts=[str(path)],
            )

        return ToolResult(
            tool=tool_name, success=True, exit_code=0, stdout_summary=str(path), stderr_summary="",
            data={
                "path": str(path), "original_content": original, "file_existed": file_existed,
                "lint_passed": lint_result["passed"] if lint_result else None,
                "lint_output": lint_result["output"] if lint_result else "",
            },
            artifacts=[str(path)],
        )

    def _looks_like_unified_diff(self, content: str) -> bool:
        text = content.lstrip()
        return text.startswith("diff --git") or text.startswith("--- ")

    def _validate_patch_request(self, path: Path, content: str) -> str:
        """Reject unsafe edit targets before any filesystem write happens."""
        if self.config is None:
            return self._validate_diff_headers_without_project(path, content)

        project_root = Path(self.config.project.root).expanduser().resolve()
        target = path.expanduser().resolve()
        try:
            relative = target.relative_to(project_root)
        except ValueError:
            return "path is outside project root"

        if not path.exists():
            return "new files are not allowed by default"
        if not path.is_file():
            return "path is not a regular file"
        if target.suffix.lower() != ".java":
            return "edit_code only allows Java source files"
        if self._is_forbidden_path(relative):
            return "path is forbidden for automated repair"
        if not self._is_under_editable_source_root(relative):
            return "path is not in editable source roots"

        header_error = self._validate_diff_headers_match_target(relative, content)
        if header_error:
            return header_error
        return ""

    def _validate_diff_headers_without_project(self, path: Path, content: str) -> str:
        old_path, new_path, error = self._extract_single_diff_header(content)
        if error:
            return error
        if old_path == "/dev/null" or new_path == "/dev/null":
            return ""
        if old_path != new_path:
            return "diff old and new paths must match"
        return ""

    def _validate_diff_headers_match_target(self, relative: Path, content: str) -> str:
        old_path, new_path, error = self._extract_single_diff_header(content)
        if error:
            return error
        if old_path == "/dev/null" or new_path == "/dev/null":
            return "new files and file deletions are not allowed by default"
        if old_path != new_path:
            return "diff old and new paths must match"
        expected = relative.as_posix()
        if new_path != expected:
            # Agent may write the absolute path in diff header; try to extract relative portion
            if self.config is not None:
                project_root = Path(self.config.project.root).expanduser().resolve()
                try:
                    resolved_relative = Path(new_path.replace("\\", "/")).resolve().relative_to(project_root)
                    if resolved_relative.as_posix() == expected:
                        return ""
                except (ValueError, OSError):
                    pass
            return "diff header path does not match target path"
        return ""

    def _extract_single_diff_header(self, content: str) -> tuple[str, str, str]:
        if content.count("diff --git ") > 1:
            return "", "", "edit_code accepts exactly one file per patch"

        old_headers: list[str] = []
        new_headers: list[str] = []
        in_hunk = False
        for line in content.splitlines():
            if line.startswith("diff --git "):
                in_hunk = False
                continue
            if line.startswith("@@"):
                in_hunk = True
                continue
            if in_hunk:
                continue
            if line.startswith("--- "):
                old_headers.append(self._normalize_diff_path(line[4:]))
            elif line.startswith("+++ "):
                new_headers.append(self._normalize_diff_path(line[4:]))

        if len(old_headers) != 1 or len(new_headers) != 1:
            return "", "", "edit_code requires exactly one diff header pair"
        return old_headers[0], new_headers[0], ""

    def _normalize_diff_path(self, raw_path: str) -> str:
        path = raw_path.split("\t", 1)[0].strip().replace("\\", "/")
        if path in {"/dev/null", "dev/null"}:
            return "/dev/null"
        if path.startswith("a/") or path.startswith("b/"):
            path = path[2:]
        return path.strip("/")

    def _is_forbidden_path(self, relative: Path) -> bool:
        parts = [part.lower() for part in relative.parts]
        if any(part in self.FORBIDDEN_DIRS for part in parts):
            return True
        name = relative.name.lower()
        if name in self.FORBIDDEN_NAMES:
            return True
        return relative.suffix.lower() in self.FORBIDDEN_SUFFIXES

    def _is_under_editable_source_root(self, relative: Path) -> bool:
        parts = tuple(part.lower() for part in relative.parts)
        for index in range(len(parts) - 2):
            if parts[index : index + 3] in self.EDITABLE_SOURCE_ROOTS:
                return True
        return False

    def _validate_patch_size(self, hunks: list[dict[str, Any]]) -> None:
        if len(hunks) > self.MAX_HUNKS:
            raise ValueError(f"patch has too many hunks: {len(hunks)} > {self.MAX_HUNKS}")

        added = 0
        deleted = 0
        for hunk in hunks:
            for op, _text in hunk["lines"]:
                if op == "+":
                    added += 1
                elif op == "-":
                    deleted += 1
        if added > self.MAX_ADDED_LINES:
            raise ValueError(f"patch adds too many lines: {added} > {self.MAX_ADDED_LINES}")
        if deleted > self.MAX_DELETED_LINES:
            raise ValueError(f"patch deletes too many lines: {deleted} > {self.MAX_DELETED_LINES}")

    def _apply_simple_unified_diff(self, original: str, diff_text: str, hunks: list[dict[str, Any]] | None = None) -> str:
        """Apply a single-file unified diff with real hunk semantics."""
        hunks = hunks or self._parse_unified_diff_hunks(diff_text)
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
                if line.strip() == "@@":
                    current = {
                        "old_start": 0,
                        "old_count": 0,
                        "new_start": 0,
                        "new_count": 0,
                        "lines": [],
                    }
                    hunks.append(current)
                    continue
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

    def _run_lint(self, file_path: Path) -> dict[str, Any] | None:
        """对编辑后的文件执行 lint 检查。返回 None 表示未配置 lint。"""
        if self.config is None:
            return None
        lint_command = self.config.project.lint_command.strip()
        if not lint_command:
            return None

        command = lint_command.replace("{file}", str(file_path))
        try:
            tokens = shlex.split(command, posix=False)
        except ValueError:
            tokens = command.split()
        tokens = [token[1:-1] if len(token) >= 2 and token[0] == token[-1] and token[0] in {"'", '"'} else token for token in tokens]

        cwd = Path(self.config.project.root) if self.config.project.root != "." else file_path.parent

        try:
            completed = subprocess.run(
                tokens,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
                check=False,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return {"passed": False, "output": "lint timed out after 30s"}
        except FileNotFoundError:
            return {"passed": False, "output": f"lint command not found: {tokens[0]}"}

        output = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
        return {"passed": completed.returncode == 0, "output": output[:4000]}
