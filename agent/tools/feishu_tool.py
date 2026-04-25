from __future__ import annotations

"""提供飞书通知工具。"""

import os
from typing import Any

import requests

from agent.models import ToolResult, ToolSpec
from agent.tools.base import BaseTool, PermissionType


class FeishuTool(BaseTool):
    """封装飞书卡片通知能力。"""

    def __init__(self, webhook: str | None = None, token_env: str = "FEISHU_WEBHOOK", dry_run: bool | None = None) -> None:
        """初始化飞书工具。"""
        self.webhook = webhook or os.getenv(token_env, "")
        self.dry_run = dry_run if dry_run is not None else not bool(self.webhook)

    @property
    def spec(self) -> ToolSpec:
        """返回飞书工具的规格说明。"""
        return ToolSpec(
            name="feishu_tool",
            description="Send Feishu notification cards",
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
        """返回飞书工具所需权限。"""
        return PermissionType.EXTERNAL_NOTIFY

    def run(self, payload: dict[str, Any] | None = None) -> ToolResult:
        """执行飞书通知。"""
        data = payload or {}
        action = str(data.get("action", "send_review_card"))
        args = data.get("args", {}) or {}
        if action == "send_review_card":
            return self._send_card(args)
        if action == "send_failed_card":
            return self._send_card(args)
        if action == "send_skill_created_card":
            return self._send_card(args)
        return ToolResult(tool="feishu_tool", success=False, exit_code=1, stdout_summary="", stderr_summary=f"unsupported action: {action}", data={"action": action}, artifacts=[])

    def _send_card(self, args: dict[str, Any]) -> ToolResult:
        """发送或模拟发送飞书卡片。"""
        if self.dry_run:
            return ToolResult(tool="feishu_tool", success=True, exit_code=0, stdout_summary="dry_run send_card", stderr_summary="", data={"card": args}, artifacts=[])
        response = requests.post(self.webhook, json={"msg_type": "interactive", "card": args}, timeout=30)
        response.raise_for_status()
        return ToolResult(tool="feishu_tool", success=True, exit_code=0, stdout_summary="card sent", stderr_summary="", data={"response": response.json() if response.content else {}}, artifacts=[])
