"""验证飞书通知与审核事件解析。"""

from agent.ingestion.feishu_bug_receiver import FeishuBugReceiver
from agent.tools.feishu_tool import FeishuTool


def test_feishu_tool_dry_run_send_review_card() -> None:
    """飞书工具应支持 review 卡片的 dry_run 发送。"""
    tool = FeishuTool(webhook="", dry_run=True)

    result = tool.run({"action": "send_review_card", "args": {"project": "demo", "bug_id": "BUG-1"}})

    assert result.success is True
    assert result.data["card"]["bug_id"] == "BUG-1"


def test_feishu_bug_receiver_parses_review_passed() -> None:
    """飞书接收器应能解析 review_passed 事件。"""
    receiver = FeishuBugReceiver()
    event = receiver.parse_review_event({"event_type": "review_passed", "bug_id": "bug-xxx", "reviewer": "developer_a", "comment": "修复正确"})

    assert event.decision == "review_passed"
    assert event.task_id == "bug-xxx"
    assert event.reviewer == "developer_a"


def test_feishu_bug_receiver_parses_review_failed() -> None:
    """飞书接收器应能解析 review_failed 事件。"""
    receiver = FeishuBugReceiver()
    event = receiver.parse_review_event({"event_type": "review_failed", "bug_id": "bug-xxx", "reviewer": "developer_a", "human_fix_branch": "human-fix/xxx", "comment": "Agent 修复层级不对"})

    assert event.decision == "review_failed"
    assert event.task_id == "bug-xxx"
    assert event.reviewer == "developer_a"
