from __future__ import annotations

"""Project-scoped Java code search tool."""

import re
from pathlib import Path
from typing import Any

from agent.models import ToolResult, ToolSpec
from agent.tools.base import BaseTool, PermissionType


class SearchCodeTool(BaseTool):
    """Search Java file contents under a project root."""

    _IGNORED_DIRS = {".git", ".gradle", "build", "target", "out"}

    @property
    def spec(self) -> ToolSpec:
        """Return the search tool metadata."""
        return ToolSpec(
            name="search_code",
            description="Search Java source files under the current project root. Returns file path, line number, snippet, and small context for each match.",
            input_schema={
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "Text or regex pattern to find in .java files."},
                    "root": {"type": "string", "description": "Optional search root. Runtime overrides this to the BugEvent project root."},
                    "regex": {"type": "boolean", "description": "Treat keyword as a regular expression. Defaults to false."},
                    "include_file_names": {"type": "boolean", "description": "Also match Java file names. Defaults to true."},
                    "context_lines": {"type": "integer", "description": "Number of lines before and after each content match. Defaults to 2, max 5."},
                    "max_results": {"type": "integer", "description": "Maximum number of match entries to return. Defaults to 20, max 100."},
                },
                "required": ["keyword"],
                "additionalProperties": False,
            },
            permission=PermissionType.READ_ONLY.value,
            executor="local",
        )

    @property
    def permission(self) -> PermissionType:
        """Return the permission required by this tool."""
        return PermissionType.READ_ONLY

    def run(self, payload: dict[str, Any] | None = None) -> ToolResult:
        """Search .java file contents and return line-level matches."""
        data = payload or {}
        root = Path(str(data.get("root", ".")))
        keyword = str(data.get("keyword", ""))
        if not keyword:
            return ToolResult(
                tool="search_code",
                success=True,
                exit_code=0,
                stdout_summary="found 0 match(es)",
                stderr_summary="",
                data={"keyword": keyword, "root": str(root), "matches": [], "total": 0, "truncated": False},
                artifacts=[],
            )

        use_regex = bool(data.get("regex", False))
        include_file_names = bool(data.get("include_file_names", True))
        context_lines = self._bounded_int(data.get("context_lines", 2), default=2, minimum=0, maximum=5)
        max_results = self._bounded_int(data.get("max_results", 20), default=20, minimum=1, maximum=100)
        pattern: re.Pattern[str] | None = None
        if use_regex:
            try:
                pattern = re.compile(keyword)
            except re.error as exc:
                return ToolResult(tool="search_code", success=False, exit_code=1, stdout_summary="", stderr_summary=f"invalid regex: {exc}", data={"keyword": keyword, "root": str(root)}, artifacts=[])

        matches: list[dict[str, Any]] = []
        matched_paths: list[str] = []
        total = 0
        truncated = False
        for path in root.rglob("*.java"):
            if self._is_ignored(path):
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue

            if include_file_names and self._text_matches(path.name, keyword, pattern):
                total += 1
                if len(matches) < max_results:
                    self._append_match(
                        matches,
                        matched_paths,
                        path=path,
                        line=0,
                        snippet=path.name,
                        before=[],
                        after=[],
                        match_type="file_name",
                    )
                else:
                    truncated = True

            for line_index, text in enumerate(lines):
                if not self._text_matches(text, keyword, pattern):
                    continue
                total += 1
                if len(matches) >= max_results:
                    truncated = True
                    continue
                start = max(0, line_index - context_lines)
                end = min(len(lines), line_index + context_lines + 1)
                self._append_match(
                    matches,
                    matched_paths,
                    path=path,
                    line=line_index + 1,
                    snippet=text,
                    before=lines[start:line_index],
                    after=lines[line_index + 1 : end],
                    match_type="content",
                )

        stdout = f"found {total} match(es) in {len(matched_paths)} file(s)"
        if truncated:
            stdout += f"; returned first {len(matches)}"
        return ToolResult(
            tool="search_code",
            success=True,
            exit_code=0,
            stdout_summary=stdout,
            stderr_summary="",
            data={
                "keyword": keyword,
                "root": str(root),
                "regex": use_regex,
                "include_file_names": include_file_names,
                "context_lines": context_lines,
                "max_results": max_results,
                "matches": matches,
                "total": total,
                "truncated": truncated,
                "files": matched_paths,
            },
            artifacts=matched_paths,
        )

    def _append_match(
        self,
        matches: list[dict[str, Any]],
        matched_paths: list[str],
        *,
        path: Path,
        line: int,
        snippet: str,
        before: list[str],
        after: list[str],
        match_type: str,
    ) -> None:
        path_text = str(path)
        matches.append(
            {
                "path": path_text,
                "line": line,
                "snippet": snippet,
                "before": before,
                "after": after,
                "match_type": match_type,
            }
        )
        if path_text not in matched_paths:
            matched_paths.append(path_text)

    def _text_matches(self, text: str, keyword: str, pattern: re.Pattern[str] | None) -> bool:
        if pattern is not None:
            return pattern.search(text) is not None
        return keyword in text

    def _bounded_int(self, value: Any, *, default: int, minimum: int, maximum: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return default
        return max(minimum, min(maximum, number))

    def _is_ignored(self, path: Path) -> bool:
        return any(part.lower() in self._IGNORED_DIRS for part in path.parts)
