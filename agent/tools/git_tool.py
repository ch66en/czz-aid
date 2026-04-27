from __future__ import annotations

"""提供 Git 操作工具。"""

import re
from typing import Any

from agent.config import AppConfig
from agent.models import ToolResult, ToolSpec
from agent.tools.base import BaseTool, PermissionType


class GitTool(BaseTool):
    """封装 Git 相关能力，默认支持 dry-run。"""

    def __init__(self, config: AppConfig | None = None) -> None:
        self.config = config or AppConfig()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="git_tool",
            description="Git helper for branch/commit/push/status/diff",
            input_schema={"type": "object", "properties": {"action": {"type": "string"}, "args": {"type": "object"}}},
            permission=PermissionType.VCS_WRITE.value,
            executor="local",
        )

    @property
    def permission(self) -> PermissionType:
        return PermissionType.VCS_WRITE

    def run(self, payload: dict[str, Any] | None = None) -> ToolResult:
        data = payload or {}
        action = str(data.get("action", "")).strip()
        args = data.get("args", {})
        args = args if isinstance(args, dict) else {}

        if action == "create_branch":
            bug_id = str(args.get("bug_id", "")).strip()
            short_title = str(args.get("short_title", "")).strip()
            if not bug_id:
                return ToolResult(tool="git_tool", success=False, exit_code=1, stderr_summary="bug_id is required", data={"action": action}, artifacts=[])
            branch = self._build_branch_name(self.config.project.name, bug_id, short_title)
            command = f"git checkout -B {branch}"
            return ToolResult(
                tool="git_tool",
                success=True,
                exit_code=0,
                stdout_summary="branch prepared",
                stderr_summary="",
                data={"action": action, "branch": branch, "command": command, "dry_run": bool(args.get("dry_run", False))},
                artifacts=[],
            )

        if action == "commit":
            message = str(args.get("message", "")).strip()
            if not message:
                return ToolResult(tool="git_tool", success=False, exit_code=1, stdout_summary="", stderr_summary="message is required", data={"action": action}, artifacts=[])
            return ToolResult(tool="git_tool", success=True, exit_code=0, stdout_summary="commit prepared", stderr_summary="", data={"action": action, "command": f'git commit -m "{message}"'}, artifacts=[])

        return ToolResult(
            tool="git_tool",
            success=True,
            exit_code=0,
            stdout_summary="git tool ready",
            stderr_summary="",
            data={"action": action},
            artifacts=[],
        )

    def _build_branch_name(self, project: str, bug_id: str, short_title: str) -> str:
        bug = self._slug(bug_id)
        title = self._slug(short_title) or "fix"
        project_slug = self._slug(project) or "project"
        return f"agent-fix/{project_slug}-{bug}-{title}"

    def _slug(self, text: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower())
        return re.sub(r"-+", "-", cleaned).strip("-")
