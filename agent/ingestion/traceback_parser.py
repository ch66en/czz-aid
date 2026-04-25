from __future__ import annotations

"""解析异常堆栈文本中的关键信息。"""

from dataclasses import dataclass


@dataclass(slots=True)
class ParsedTraceback:
    """表示从堆栈文本中提取出的异常类型与信息。"""

    error_type: str
    message: str


class TracebackParser:
    """负责从 traceback 文本中提取末行异常摘要。"""

    def parse(self, text: str) -> ParsedTraceback:
        """解析输入文本并返回结构化异常信息。"""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return ParsedTraceback(error_type="UnknownError", message="")

        last_line = lines[-1]
        if ":" in last_line:
            error_type, message = last_line.split(":", 1)
            return ParsedTraceback(error_type=error_type.strip(), message=message.strip())
        return ParsedTraceback(error_type="UnknownError", message=last_line)
