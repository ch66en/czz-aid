from __future__ import annotations

"""提供 Gitee PR 工具。"""

import os
import re
from typing import Any

import requests

from agent.config import AppConfig
from agent.models import ToolResult, ToolSpec
from agent.tools.base import BaseTool, PermissionType


class GiteeTool(BaseTool):
    """封装 Gitee PR 能力，默认支持 dry-run。"""

    def __init__(self, config: AppConfig | None = None, session: Any | None = None) -> None:
        self.config = config or AppConfig()
        self.session = session or requests.Session()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="gitee_tool",
            description="Create/get Gitee pull request",
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

        if action != "create_pull_request":
            return ToolResult(
                tool="gitee_tool",
                success=False,
                exit_code=1,
                stdout_summary="",
                stderr_summary=f"unsupported action: {action}",
                data={"action": action},
                artifacts=[],
            )

        token = os.getenv("GITEE_TOKEN") or os.getenv("GITEE_ACCESS_TOKEN") or self.config.gitee.token
        dry_run = not bool(token)
        bug_id = str(args.get("bug_id", "")).strip()
        short_title = str(args.get("short_title", "")).strip()
        exception_type = str(args.get("exception_type", "")).strip()
        project_name = self.config.project.name
        head = self._build_branch_name(project_name, bug_id, short_title)
        base = self.config.project.default_branch
        title = f"[Agent Fix] 修复 {exception_type or short_title or bug_id or '异常'}"
        body = self._build_pr_body(args)

        result_data = {
            "dry_run": dry_run,
            "auto_merge": False,
            "head": head,
            "base": base,
            "title": title,
            "body": body,
        }

        if dry_run:
            result_data["url"] = f"dry_run://gitee/{self.config.gitee.owner}/{self.config.gitee.repo}/pulls/{head}"
            return ToolResult(tool="gitee_tool", success=True, exit_code=0, stdout_summary="dry run pr generated", stderr_summary="", data=result_data, artifacts=[])

        url = f"{self.config.gitee.base_url}/repos/{self.config.gitee.owner}/{self.config.gitee.repo}/pulls"
        payload_data = {
            "access_token": token,
            "title": title,
            "head": head,
            "base": base,
            "body": body,
        }
        try:
            resp = self.session.post(url, json=payload_data, timeout=15)
            ok = getattr(resp, "status_code", 500) < 400
            if ok:
                pr_data = resp.json() if hasattr(resp, "json") else {}
                result_data["url"] = pr_data.get("html_url", "")
                return ToolResult(tool="gitee_tool", success=True, exit_code=0, stdout_summary="pr created", stderr_summary="", data=result_data, artifacts=[])
            return ToolResult(tool="gitee_tool", success=False, exit_code=1, stdout_summary="", stderr_summary=str(getattr(resp, "text", "request failed")), data=result_data, artifacts=[])
        except Exception as exc:
            return ToolResult(tool="gitee_tool", success=False, exit_code=1, stdout_summary="", stderr_summary=str(exc), data=result_data, artifacts=[])

    def _build_branch_name(self, project: str, bug_id: str, short_title: str) -> str:
        return f"agent-fix/{self._slug(project)}-{self._slug(bug_id)}-{self._slug(short_title) or 'fix'}"

    def _slug(self, text: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower())
        return re.sub(r"-+", "-", cleaned).strip("-")

    def _build_pr_body(self, args: dict[str, Any]) -> str:
        compile_result = self._as_tool_result(args.get("compile_result"))
        test_result = self._as_tool_result(args.get("test_result"))
        changed_files = args.get("changed_files", [])
        if not isinstance(changed_files, list):
            changed_files = []
        changed_text = "\n".join(f"- {item}" for item in changed_files) or "- 无"
        return "\n\n".join(
            [
                "## Bug 摘要\n" + f"- bug_id: {args.get('bug_id', '')}\n- 异常: {args.get('exception_type', '')}\n- 信息: {args.get('message', '')}",
                "## 根因分析\n" + str(args.get("root_cause", "")),
                "## 修复方案\n" + str(args.get("fix_plan", "")),
                "## 修改文件\n" + changed_text,
                "## mvn compile 结果\n" + f"- success: {compile_result.success}\n- summary: {compile_result.stdout_summary or compile_result.stderr_summary}",
                "## mvn test 结果\n" + f"- success: {test_result.success}\n- summary: {test_result.stdout_summary or test_result.stderr_summary}",
                "## 风险说明\n" + str(args.get("risk", "")),
                "## session 路径\n" + str(args.get("session_path", "")),
                "## Review 提醒\n- Agent 不会自动合并 PR，请人工 review 后再决策。",
            ]
        )

    def _as_tool_result(self, value: Any) -> ToolResult:
        if isinstance(value, ToolResult):
            return value
        if isinstance(value, dict):
            try:
                return ToolResult.model_validate(value)
            except Exception:
                pass
        return ToolResult(tool="unknown", success=False, exit_code=1, stdout_summary="", stderr_summary="", data={}, artifacts=[])
