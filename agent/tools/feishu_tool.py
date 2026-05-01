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
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["send_help_card", "send_review_request_card", "send_skill_created_card"],
                        "description": "Feishu notification action.",
                    },
                    "args": {"type": "object", "description": "Notification payload."},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
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
            message = self._build_help_message(args)
            card = self._build_help_card(args, message)
            return self._deliver(webhook, card, message, dry_run=not bool(webhook), extra={"action": action})

        if action == "send_review_request_card":
            message = self._build_review_message(args)
            card = self._build_review_card(args, message)
            return self._deliver(webhook, card, message, dry_run=not bool(webhook), extra={"action": action})

        if action == "send_skill_created_card":
            message = self._build_skill_created_message(args)
            card = self._build_skill_created_card(args, message)
            return self._deliver(webhook, card, message, dry_run=not bool(webhook), extra={"action": action})

        return ToolResult(
            tool="feishu_tool",
            success=False,
            exit_code=1,
            stdout_summary="",
            stderr_summary=f"unsupported action: {action}",
            data={"action": action},
            artifacts=[],
        )

    def _deliver(self, webhook: str, payload: dict[str, Any], message: str, dry_run: bool, extra: dict[str, Any]) -> ToolResult:
        if dry_run:
            return ToolResult(
                tool="feishu_tool",
                success=True,
                exit_code=0,
                stdout_summary="dry run",
                stderr_summary="",
                data={"dry_run": True, "message": message, "payload": payload, **extra},
                artifacts=[],
            )

        try:
            resp = self.session.post(webhook, json=payload, timeout=10)
            ok = getattr(resp, "status_code", 500) < 400
            error_text = str(getattr(resp, "text", "request failed"))
            if ok and hasattr(resp, "json"):
                try:
                    response_data = resp.json()
                except Exception:
                    response_data = {}
                if isinstance(response_data, dict) and int(response_data.get("code", 0) or 0) != 0:
                    ok = False
                    error_text = str(response_data.get("msg") or response_data)
            return ToolResult(
                tool="feishu_tool",
                success=ok,
                exit_code=0 if ok else 1,
                stdout_summary="sent" if ok else "send failed",
                stderr_summary="" if ok else error_text,
                data={"dry_run": False, "message": message, "payload": payload, **extra},
                artifacts=[],
            )
        except Exception as exc:
            return ToolResult(
                tool="feishu_tool",
                success=False,
                exit_code=1,
                stdout_summary="",
                stderr_summary=str(exc),
                data={"dry_run": False, "message": message, "payload": payload, **extra},
                artifacts=[],
            )

    def _build_help_message(self, args: dict[str, Any]) -> str:
        bug = self._bug(args)
        last_result = self._mapping(args.get("last_result"))
        return (
            "自动修复失败，请人工介入。\n"
            f"项目：{bug.get('project', '')}\n"
            f"Bug ID：{bug.get('bug_id', 'unknown')}\n"
            f"异常：{bug.get('exception_type', '')}\n"
            f"位置：{bug.get('top_business_frame', '')}\n"
            f"失败工具：{last_result.get('tool', '')}\n"
            f"失败摘要：{last_result.get('stderr_summary') or last_result.get('stdout_summary') or ''}"
        ).strip()

    def _build_review_message(self, args: dict[str, Any]) -> str:
        bug = self._bug(args)
        bug_id = str(args.get("bug_id") or bug.get("bug_id") or "unknown")
        return (
            "自动修复已通过验证，请人工 Review。\n"
            f"项目：{bug.get('project', '')}\n"
            f"Bug ID：{bug_id}\n"
            f"异常：{bug.get('exception_type', '')}\n"
            f"位置：{bug.get('top_business_frame', '')}\n"
            f"PR：{args.get('pr_url', '')}"
        ).strip()

    def _build_skill_created_message(self, args: dict[str, Any]) -> str:
        return (
            "Skill 已生成并上传至云端共享，团队成员可直接复用。\n"
            f"Bug ID：{args.get('bug_id', 'unknown')}\n"
            f"Skill：{args.get('skill_name', '')}\n"
            f"路径：{args.get('skill_path', '')}"
        ).strip()

    def _build_help_card(self, args: dict[str, Any], message: str) -> dict[str, Any]:
        bug = self._bug(args)
        last_result = self._mapping(args.get("last_result"))
        fields = [
            ("项目", bug.get("project", "")),
            ("Bug ID", bug.get("bug_id", "unknown")),
            ("异常类型", bug.get("exception_type", "")),
            ("异常位置", bug.get("top_business_frame", "")),
            ("失败工具", last_result.get("tool", "")),
            ("失败摘要", last_result.get("stderr_summary") or last_result.get("stdout_summary") or ""),
            ("建议操作", "人工介入，并在 review_failed 时填写 human_fix_branch"),
        ]
        return self._interactive_card(
            title="自动修复失败求助",
            color="red",
            message=message,
            fields=fields,
            actions=[],
        )

    def _build_review_card(self, args: dict[str, Any], message: str) -> dict[str, Any]:
        bug = self._bug(args)
        bug_id = str(args.get("bug_id") or bug.get("bug_id") or "unknown")
        compile_result = self._mapping(args.get("compile_result"))
        test_result = self._mapping(args.get("test_result"))
        pr_url = str(args.get("pr_url", ""))
        fields = [
            ("项目", bug.get("project", "")),
            ("Bug ID", bug_id),
            ("异常类型", bug.get("exception_type", "")),
            ("异常位置", bug.get("top_business_frame", "")),
            ("PR", pr_url),
            ("编译结果", self._result_text(compile_result)),
            ("测试结果", self._result_text(test_result)),
            ("修复分支", args.get("agent_branch", "")),
            ("目标分支", args.get("base_branch", "")),
        ]
        actions: list[dict[str, Any]] = []
        if pr_url:
            actions.append({"tag": "button", "text": {"tag": "plain_text", "content": "打开 PR"}, "type": "primary", "url": pr_url})
        actions.append(self._review_button("审核通过", "primary", args.get("review_pass_url"), {"event_type": "review_passed", "bug_id": bug_id}))
        actions.append(self._review_button("审核失败", "danger", args.get("review_fail_url"), {"event_type": "review_failed", "bug_id": bug_id, "requires": "human_fix_branch"}))
        return self._interactive_card(title="自动修复 Review", color="green", message=message, fields=fields, actions=actions)

    def _build_skill_created_card(self, args: dict[str, Any], message: str) -> dict[str, Any]:
        fields = [
            ("Bug ID", args.get("bug_id", "unknown")),
            ("Skill", args.get("skill_name", "")),
            ("路径", args.get("skill_path", "")),
            ("状态", "已上传至云端共享"),
        ]
        return self._interactive_card(title="Skill 已上传至云端共享", color="blue", message=message, fields=fields, actions=[])

    def _interactive_card(self, *, title: str, color: str, message: str, fields: list[tuple[str, Any]], actions: list[dict[str, Any]]) -> dict[str, Any]:
        elements: list[dict[str, Any]] = [
            {"tag": "div", "text": {"tag": "lark_md", "content": self._md(message)}},
        ]
        field_lines = [f"**{name}**：{self._md(str(value))}" for name, value in fields if str(value).strip()]
        if field_lines:
            elements.append({"tag": "hr"})
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(field_lines)}})
        if actions:
            elements.append({"tag": "action", "actions": actions})
        return {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {"template": color, "title": {"tag": "plain_text", "content": title}},
                "elements": elements,
            },
        }

    def _bug(self, args: dict[str, Any]) -> dict[str, Any]:
        return self._mapping(args.get("bug"))

    def _mapping(self, value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _result_text(self, value: dict[str, Any]) -> str:
        if not value:
            return ""
        summary = value.get("stdout_summary") or value.get("stderr_summary") or ""
        return f"success={value.get('success')} exit_code={value.get('exit_code')} {summary}".strip()

    def _review_button(self, text: str, button_type: str, url: Any, fallback_value: dict[str, Any]) -> dict[str, Any]:
        button = {"tag": "button", "text": {"tag": "plain_text", "content": text}, "type": button_type}
        if str(url or "").strip():
            button["url"] = str(url)
        else:
            button["value"] = fallback_value
        return button

    def _md(self, text: str) -> str:
        return text.replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_").replace("`", "\\`")
