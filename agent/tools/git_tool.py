from __future__ import annotations

"""提供 Git 操作工具。"""

import subprocess
from pathlib import Path
from typing import Any

from agent.models import ToolResult, ToolSpec
from agent.tools.base import BaseTool, PermissionType


class GitTool(BaseTool):
    """封装 Git 常用操作。"""

    def __init__(self, repo_path: str | None = None) -> None:
        """初始化 Git 工具。"""
        self.repo_path = Path(repo_path or ".").resolve()

    @property
    def spec(self) -> ToolSpec:
        """返回 Git 工具的规格说明。"""
        return ToolSpec(
            name="git_tool",
            description="Git status, diff, branch, add, commit and push",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "args": {"type": "object"},
                },
                "required": ["action"],
            },
            permission=PermissionType.VCS_WRITE.value,
            executor="local",
        )

    @property
    def permission(self) -> PermissionType:
        """返回 Git 工具所需权限。"""
        return PermissionType.VCS_WRITE

    def run(self, payload: dict[str, Any] | None = None) -> ToolResult:
        """执行 Git 子命令。"""
        data = payload or {}
        action = str(data.get("action", "status"))
        args = data.get("args", {}) or {}
        if action == "status":
            return self._run_git(["status", "--short", "--branch"])
        if action == "diff":
            return self._run_git(["diff"])
        if action == "create_branch":
            branch = str(args.get("branch", ""))
            return self._run_git(["checkout", "-b", branch])
        if action == "add":
            paths = args.get("paths", ["."])
            return self._run_git(["add", *list(paths)])
        if action == "commit":
            message = str(args.get("message", ""))
            return self._run_git(["commit", "-m", message])
        if action == "push":
            remote = str(args.get("remote", "origin"))
            branch = str(args.get("branch", "HEAD"))
            return self._run_git(["push", remote, branch])
        return ToolResult(tool="git_tool", success=False, exit_code=1, stdout_summary="", stderr_summary=f"unsupported action: {action}", data={"action": action}, artifacts=[])

    def _run_git(self, args: list[str]) -> ToolResult:
        """执行 Git 命令并返回统一结果。"""
        completed = subprocess.run(["git", *args], cwd=self.repo_path, capture_output=True, text=True, shell=False, check=False)
        return ToolResult(
            tool="git_tool",
            success=completed.returncode == 0,
            exit_code=completed.returncode,
            stdout_summary=(completed.stdout or "").strip()[:4000],
            stderr_summary=(completed.stderr or "").strip()[:4000],
            data={"args": args},
            artifacts=[],
        )
