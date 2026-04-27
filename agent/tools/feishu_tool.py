from __future__ import annotations

"""提供飞书通知工具。"""

import os
from typing import Any

import requests

from agent.config import AppConfig
from agent.models import ToolResult, ToolSpec
from agent.tools.base import BaseTool, PermissionType


class FeishuTool(BaseTool):
    """封装飞书通知能力，支持 dry-run。"""

    def __init__(self, config: AppConfig | None = None, session: Any | None = None) -> None:
        self.config = config or AppConfig()
        self.session = session or requests.Session()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="feishu_tool",
            description="Send feishu help/review card",
            input_schema={"type": "object", "properties": {"action": {"type": "string"}, "args": {"type": "object"}}},
            permission=PermissionType.EXTERNAL_NOTIFY.value,
            executor="local",
        )

    @property
    def permission(self) -> PermissionType:
        return PermissionType.EXTERNAL_NOTIFY

    def run(self, payload: dict[str, Any] | None = None) -> ToolResult:
        data = payload or {}
        action = str(data.get("action", "")).strip()
        args = data.get("args", {})
        args = args if isinstance(args, dict) else {}

        webhook = os.getenv("FEISHU_WEBHOOK") or self.config.feishu.webhook
        if action == "send_help_card":
            bug = args.get("bug", {}) if isinstance(args.get("bug"), dict) else {}
            bug_id = str(bug.get("bug_id", "unknown"))
            message = f"自动修复失败，请人工介入。bug={bug_id}"
            return self._deliver(webhook, message, dry_run=not bool(webhook), extra={"action": action})

        if action == "send_review_request_card":
            bug_id = str(args.get("bug_id", "unknown"))
            pr_url = str(args.get("pr_url", ""))
            message = f"review_failed: 请人工审核 bug={bug_id} pr={pr_url}"
            return self._deliver(webhook, message, dry_run=not bool(webhook), extra={"action": action})

        return ToolResult(
            tool="feishu_tool",
            success=False,
            exit_code=1,
            stdout_summary="",
            stderr_summary=f"unsupported action: {action}",
            data={"action": action},
            artifacts=[],
        )

    def _deliver(self, webhook: str, message: str, dry_run: bool, extra: dict[str, Any]) -> ToolResult:
        if dry_run:
            return ToolResult(
                tool="feishu_tool",
                success=True,
                exit_code=0,
                stdout_summary="dry run",
                stderr_summary="",
                data={"dry_run": True, "message": message, **extra},
                artifacts=[],
            )

        try:
            resp = self.session.post(webhook, json={"msg_type": "text", "content": {"text": message}}, timeout=10)
            ok = getattr(resp, "status_code", 500) < 400
            return ToolResult(
                tool="feishu_tool",
                success=ok,
                exit_code=0 if ok else 1,
                stdout_summary="sent" if ok else "send failed",
                stderr_summary="" if ok else str(getattr(resp, "text", "request failed")),
                data={"dry_run": False, "message": message, **extra},
                artifacts=[],
            )
        except Exception as exc:
            return ToolResult(
                tool="feishu_tool",
                success=False,
                exit_code=1,
                stdout_summary="",
                stderr_summary=str(exc),
                data={"dry_run": False, "message": message, **extra},
                artifacts=[],
            )
