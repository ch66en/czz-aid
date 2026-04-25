from __future__ import annotations

"""处理来自飞书渠道的缺陷上报与审核事件。"""

from dataclasses import dataclass
from typing import Any

from agent.models import BugReport, ReviewDecision, ReviewEvent


@dataclass(slots=True)
class FeishuBugReceiverResult:
    """表示飞书输入解析后的统一结果。"""

    kind: str
    data: BugReport | ReviewEvent


class FeishuBugReceiver:
    """将飞书负载转换为统一对象。"""

    def receive(self, payload: dict[str, Any]) -> BugReport | ReviewEvent:
        """接收飞书消息负载并构造缺陷报告或审核事件。"""
        event_type = str(payload.get("event_type", "bug_report"))
        if event_type in {ReviewDecision.REVIEW_PASSED.value, ReviewDecision.REVIEW_FAILED.value}:
            return self.parse_review_event(payload)
        return BugReport(
            bug_id=str(payload.get("bug_id", "")),
            title=str(payload.get("title", "")),
            content=str(payload.get("content", "")),
            source="feishu",
        )

    def parse_review_event(self, payload: dict[str, Any]) -> ReviewEvent:
        """解析人工审核事件。"""
        event_type = str(payload.get("event_type", ""))
        if event_type not in {ReviewDecision.REVIEW_PASSED.value, ReviewDecision.REVIEW_FAILED.value}:
            raise ValueError(f"unsupported review event type: {event_type}")
        decision = ReviewDecision(event_type)
        return ReviewEvent(
            task_id=str(payload.get("bug_id", "")),
            reviewer=str(payload.get("reviewer", "")),
            decision=decision.value,
            comment=str(payload.get("comment", "")),
        )
