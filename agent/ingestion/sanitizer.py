from __future__ import annotations

"""提供日志与文本中的敏感信息脱敏能力。"""

import re


class Sanitizer:
    """负责屏蔽邮箱与手机号等敏感字段。"""

    def sanitize(self, text: str) -> str:
        """对输入文本执行敏感信息脱敏。"""
        # 先屏蔽邮箱地址，再继续处理其他敏感信息。
        sanitized = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+", "[EMAIL]", text)
        sanitized = re.sub(r"\b\d{11}\b", "[PHONE]", sanitized)
        return sanitized
