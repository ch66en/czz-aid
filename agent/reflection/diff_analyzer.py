from __future__ import annotations

"""分析 Agent diff 与人工 diff 的差异。"""

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class DiffAnalysis:
    """表示 diff 分析结果。"""

    agent_diff: str
    human_diff: str
    summary: str


class DiffAnalyzer:
    """对比 Agent 与人工修复差异。"""

    def analyze(self, agent_diff: str, human_diff: str) -> DiffAnalysis:
        """生成差异摘要。"""
        return DiffAnalysis(agent_diff=agent_diff, human_diff=human_diff, summary=self._summarize(agent_diff, human_diff))

    def _summarize(self, agent_diff: str, human_diff: str) -> str:
        """生成简要对比摘要。"""
        agent_lines = [line for line in agent_diff.splitlines() if line.startswith(("+", "-"))]
        human_lines = [line for line in human_diff.splitlines() if line.startswith(("+", "-"))]
        return f"agent_changes={len(agent_lines)}; human_changes={len(human_lines)}"
