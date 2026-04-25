from __future__ import annotations

"""处理来自飞书渠道的缺陷上报数据。"""

from agent.models import BugReport, ReviewDecision, ReviewEvent


class FeishuBugReceiver:
    """将飞书负载转换为统一对象。"""

    def receive(self, payload: dict[str, str]) -> BugReport | ReviewEvent:
        """接收飞书消息负载并构造缺陷报告或审核事件。"""
        event_type = str(payload.get("event_type", "bug_report"))
        if event_type in {ReviewDecision.REVIEW_PASSED.value, ReviewDecision.REVIEW_FAILED.value}:
            return self.parse_review_event(payload)
        return BugReport(
            bug_id=payload.get("bug_id", ""),
            title=payload.get("title", ""),
            content=payload.get("content", ""),
            source="feishu",
        )

    def parse_review_event(self, payload: dict[str, str]) -> ReviewEvent:
        """解析人工审核事件。"""
        event_type = str(payload.get("event_type", ""))
        if event_type not in {ReviewDecision.REVIEW_PASSED.value, ReviewDecision.REVIEW_FAILED.value}:
            raise ValueError(f"unsupported review event type: {event_type}")
        return ReviewEvent(
            task_id=str(payload.get("bug_id", "")),
            reviewer=str(payload.get("reviewer", "")),
            decision=event_type,
            comment=str(payload.get("comment", "")),
        )
