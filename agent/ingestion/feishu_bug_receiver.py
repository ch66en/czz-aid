from __future__ import annotations

"""处理来自飞书渠道的缺陷上报数据。"""

from agent.models import BugReport


class FeishuBugReceiver:
    """将飞书负载转换为统一的缺陷报告对象。"""

    def receive(self, payload: dict[str, str]) -> BugReport:
        """接收飞书消息负载并构造缺陷报告。"""
        return BugReport(
            bug_id=payload.get("bug_id", ""),
            title=payload.get("title", ""),
            content=payload.get("content", ""),
            source="feishu",
        )
