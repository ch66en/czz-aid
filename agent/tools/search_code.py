from __future__ import annotations

"""Project-scoped Java code search tool."""

from pathlib import Path
from typing import Any

from agent.models import ToolResult, ToolSpec
from agent.tools.base import BaseTool, PermissionType


class SearchCodeTool(BaseTool):
    """Search Java file contents under a project root."""

    @property
    def spec(self) -> ToolSpec:
        """Return the search tool metadata."""
        return ToolSpec(
            name="search_code",
            description="Search Java source file contents for an exact keyword under the current project root. Returns matching file paths only; it does not match file names or return line numbers.",
            input_schema={
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "Exact text to find inside .java files."},
                    "root": {"type": "string", "description": "Optional search root. Runtime overrides this to the BugEvent project root."},
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
        """Search .java file contents and return matching file paths."""
        data = payload or {}
        root = Path(str(data.get("root", ".")))
        keyword = str(data.get("keyword", ""))
        matches: list[str] = []
        for path in root.rglob("*.java"):
            try:
                if keyword and keyword in path.read_text(encoding="utf-8"):
                    matches.append(str(path))
            except OSError:
                continue
        return ToolResult(tool="search_code", success=True, exit_code=0, stdout_summary=f"found {len(matches)} file(s)", stderr_summary="", data={"keyword": keyword, "root": str(root), "matches": matches}, artifacts=matches)
