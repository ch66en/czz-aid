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
    assert result.data["payload"]["msg_type"] == "interactive"
    assert result.data["payload"]["card"]["header"]["title"]["content"] == "自动修复失败求助"


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
                "review_pass_url": "http://127.0.0.1:8765/review?event_type=review_passed&bug_id=BUG-2",
                "review_fail_url": "http://127.0.0.1:8765/review?event_type=review_failed&bug_id=BUG-2",
            },
        }
    )

    assert result.success is True
    assert result.data["dry_run"] is True
    assert "请人工 Review" in result.data["message"]
    payload_text = str(result.data["payload"])
    assert "review_passed" in payload_text
    assert "review_failed" in payload_text
    assert "打开 PR" in payload_text
    assert "http://127.0.0.1:8765/review" in payload_text


def test_feishu_tool_skill_created_card_dry_run_without_webhook() -> None:
    tool = FeishuTool(AppConfig(feishu={"webhook": ""}), session=NoNetworkSession())  # type: ignore[arg-type]

    result = tool.run(
        {
            "action": "send_skill_created_card",
            "args": {"bug_id": "BUG-3", "skill_name": "skill-demo", "skill_path": "skills/skill-demo"},
        }
    )

    assert result.success is True
    assert result.data["dry_run"] is True
    assert "skill-demo" in result.data["message"]
    assert result.data["payload"]["card"]["header"]["title"]["content"] == "Skill 已生成"
