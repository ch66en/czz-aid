from __future__ import annotations

"""分析修复差异文本的基础信息。"""


class DiffAnalyzer:
    """负责生成差异内容的简要摘要。"""

    def summarize(self, diff_text: str) -> str:
        """根据差异文本长度生成最小摘要。"""
        return f"diff length={len(diff_text)}"
