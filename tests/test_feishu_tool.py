from __future__ import annotations

from agent.config import AppConfig
from agent.tools.feishu_tool import FeishuTool


class NoNetworkSession:
    def post(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("network should not be called in dry_run")


def test_feishu_tool_help_card_dry_run_without_webhook() -> None:
    tool = FeishuTool(AppConfig(feishu={"webhook": ""}), session=NoNetworkSession())  # type: ignore[arg-type]

    result = tool.run(
        {
            "action": "send_help_card",
            "args": {
                "bug": {"bug_id": "BUG-1", "project": "demo", "title": "NPE", "exception_type": "NullPointerException"},
                "last_result": {"tool": "run_test", "stderr_summary": "test failed"},
                "session_path": "data/sessions/BUG-1",
            },
        }
    )

    assert result.success is True
    assert result.data["dry_run"] is True
    assert "自动修复失败" in result.data["message"]
    assert "BUG-1" in result.data["message"]


def test_feishu_tool_review_request_dry_run_without_webhook() -> None:
    tool = FeishuTool(AppConfig(feishu={"webhook": ""}), session=NoNetworkSession())  # type: ignore[arg-type]

    result = tool.run(
        {
            "action": "send_review_request_card",
            "args": {
                "bug_id": "BUG-2",
                "pr_url": "dry_run://gitee/demo/repo/pulls/agent-fix/demo-bug-2-fix",
                "agent_branch": "agent-fix/demo-bug-2-fix",
                "base_branch": "main",
            },
        }
    )

    assert result.success is True
    assert result.data["dry_run"] is True
    assert "review_failed" in result.data["message"]
