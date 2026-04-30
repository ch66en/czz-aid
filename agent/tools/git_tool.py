from __future__ import annotations

"""提供 Git 操作工具。"""

import re
import subprocess
from pathlib import Path
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
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["create_branch", "commit", "diff"], "description": "Git operation to run."},
                    "args": {"type": "object", "description": "Operation-specific arguments."},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
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

        if action == "diff":
            return self._run_diff(args)

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

    def _run_diff(self, args: dict[str, Any]) -> ToolResult:
        base = str(args.get("base", "")).strip()
        target = str(args.get("target", "")).strip()
        cwd = self._project_root()
        if cwd is None:
            return ToolResult(tool="git_tool", success=False, exit_code=1, stdout_summary="", stderr_summary="project root not found", data={"action": "diff", "base": base, "target": target}, artifacts=[])

        command = ["git", "diff", "--no-ext-diff", "--no-color"]
        if base and target:
            command.append(f"{base}...{target}")
        elif target:
            command.append(target)
        paths = args.get("paths", [])
        if isinstance(paths, list) and paths:
            command.append("--")
            command.extend(str(path) for path in paths)

        completed = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace", shell=False, check=False)
        output = completed.stdout or ""
        return ToolResult(
            tool="git_tool",
            success=completed.returncode == 0,
            exit_code=completed.returncode,
            stdout_summary=output,
            stderr_summary=(completed.stderr or "").strip(),
            data={"action": "diff", "base": base, "target": target, "command": command, "cwd": str(cwd), "changed_files": self._changed_files(output)},
            artifacts=[],
        )

    def _project_root(self) -> Path | None:
        root = Path(self.config.project.root)
        if root.exists():
            return root
        return None

    def _changed_files(self, diff_text: str) -> list[str]:
        files: list[str] = []
        for line in diff_text.splitlines():
            if not line.startswith("diff --git "):
                continue
            parts = line.split()
            if len(parts) < 4:
                continue
            path = parts[3]
            if path.startswith("b/"):
                path = path[2:]
            if path not in files:
                files.append(path)
        return files
