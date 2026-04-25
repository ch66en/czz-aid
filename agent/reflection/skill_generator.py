from __future__ import annotations

"""根据反思摘要生成技能条目内容。"""


class SkillGenerator:
    """负责把反思结论转换为可保存的技能文本。"""

    def generate(self, summary: str) -> str:
        """根据摘要生成最小技能字符串。"""
        return f"skill:{summary}"
