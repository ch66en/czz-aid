from __future__ import annotations

"""提供 Gitee Pull Request 工具。"""

import os
from typing import Any

import requests

from agent.models import ToolResult, ToolSpec
from agent.tools.base import BaseTool, PermissionType


class GiteeTool(BaseTool):
    """封装 Gitee PR 创建与查询。"""

    def __init__(self, owner: str, repo: str, base_url: str = "https://gitee.com/api/v5", token_env: str = "GITEE_TOKEN", dry_run: bool | None = None) -> None:
        """初始化 Gitee 工具。"""
        self.owner = owner
        self.repo = repo
        self.base_url = base_url.rstrip("/")
        self.token = os.getenv(token_env, "")
        self.dry_run = dry_run if dry_run is not None else not bool(self.token)

    @property
    def spec(self) -> ToolSpec:
        """返回 Gitee 工具的规格说明。"""
        return ToolSpec(
            name="gitee_tool",
            description="Create or query Gitee pull requests",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "args": {"type": "object"},
                },
                "required": ["action"],
            },
            permission=PermissionType.EXTERNAL_NOTIFY.value,
            executor="http",
        )

    @property
    def permission(self) -> PermissionType:
        """返回 Gitee 工具所需权限。"""
        return PermissionType.EXTERNAL_NOTIFY

    def run(self, payload: dict[str, Any] | None = None) -> ToolResult:
        """执行 Gitee PR 操作。"""
        data = payload or {}
        action = str(data.get("action", "create_pull_request"))
        args = data.get("args", {}) or {}
        if action == "create_pull_request":
            return self._create_pull_request(args)
        if action == "get_pull_request":
            return self._get_pull_request(args)
        return ToolResult(tool="gitee_tool", success=False, exit_code=1, stdout_summary="", stderr_summary=f"unsupported action: {action}", data={"action": action}, artifacts=[])

    def _create_pull_request(self, args: dict[str, Any]) -> ToolResult:
        """创建或模拟创建 PR。"""
        title = str(args.get("title", ""))
        body = str(args.get("body", ""))
        head = str(args.get("head", ""))
        base = str(args.get("base", "main"))
        if self.dry_run:
            return ToolResult(tool="gitee_tool", success=True, exit_code=0, stdout_summary="dry_run create_pull_request", stderr_summary="", data={"pr_url": f"dry-run://{self.owner}/{self.repo}", "title": title, "body": body, "head": head, "base": base}, artifacts=[])
        url = f"{self.base_url}/repos/{self.owner}/{self.repo}/pulls"
        headers = {"Authorization": f"token {self.token}"}
        response = requests.post(url, headers=headers, json={"title": title, "body": body, "head": head, "base": base}, timeout=30)
        response.raise_for_status()
        payload = response.json()
        return ToolResult(tool="gitee_tool", success=True, exit_code=0, stdout_summary="pull request created", stderr_summary="", data={"pr_url": payload.get("html_url") or payload.get("url"), "response": payload}, artifacts=[])

    def _get_pull_request(self, args: dict[str, Any]) -> ToolResult:
        """查询 PR 信息。"""
        number = str(args.get("number", ""))
        if self.dry_run:
            return ToolResult(tool="gitee_tool", success=True, exit_code=0, stdout_summary="dry_run get_pull_request", stderr_summary="", data={"number": number, "pr_url": f"dry-run://{self.owner}/{self.repo}/pulls/{number}"}, artifacts=[])
        url = f"{self.base_url}/repos/{self.owner}/{self.repo}/pulls/{number}"
        headers = {"Authorization": f"token {self.token}"}
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        payload = response.json()
        return ToolResult(tool="gitee_tool", success=True, exit_code=0, stdout_summary="pull request fetched", stderr_summary="", data={"response": payload, "pr_url": payload.get("html_url") or payload.get("url")}, artifacts=[])
