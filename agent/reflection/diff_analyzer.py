from __future__ import annotations

"""分析 Agent diff 与人工 diff 的差异。"""

from dataclasses import dataclass


@dataclass(slots=True)
class DiffAnalysis:
    """表示 diff 分析结果。"""

    agent_diff: str
    human_diff: str
    summary: str
    agent_files: list[str]
    human_files: list[str]
    common_files: list[str]
    agent_only_files: list[str]
    human_only_files: list[str]
    agent_added_lines: int
    agent_removed_lines: int
    human_added_lines: int
    human_removed_lines: int
    observations: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "summary": self.summary,
            "agent_files": self.agent_files,
            "human_files": self.human_files,
            "common_files": self.common_files,
            "agent_only_files": self.agent_only_files,
            "human_only_files": self.human_only_files,
            "agent_added_lines": self.agent_added_lines,
            "agent_removed_lines": self.agent_removed_lines,
            "human_added_lines": self.human_added_lines,
            "human_removed_lines": self.human_removed_lines,
            "observations": self.observations,
        }


class DiffAnalyzer:
    """对比 Agent 与人工修复差异。"""

    def analyze(self, agent_diff: str, human_diff: str) -> DiffAnalysis:
        """生成差异摘要。"""
        agent_files = self._changed_files(agent_diff)
        human_files = self._changed_files(human_diff)
        common_files = sorted(set(agent_files) & set(human_files))
        agent_only_files = sorted(set(agent_files) - set(human_files))
        human_only_files = sorted(set(human_files) - set(agent_files))
        agent_added, agent_removed = self._line_counts(agent_diff)
        human_added, human_removed = self._line_counts(human_diff)
        observations = self._observations(common_files, agent_only_files, human_only_files)
        summary = (
            f"agent_files={len(agent_files)}; human_files={len(human_files)}; "
            f"common_files={len(common_files)}; agent_only={len(agent_only_files)}; human_only={len(human_only_files)}; "
            f"agent_lines=+{agent_added}/-{agent_removed}; human_lines=+{human_added}/-{human_removed}"
        )
        return DiffAnalysis(
            agent_diff=agent_diff,
            human_diff=human_diff,
            summary=summary,
            agent_files=agent_files,
            human_files=human_files,
            common_files=common_files,
            agent_only_files=agent_only_files,
            human_only_files=human_only_files,
            agent_added_lines=agent_added,
            agent_removed_lines=agent_removed,
            human_added_lines=human_added,
            human_removed_lines=human_removed,
            observations=observations,
        )

    def _changed_files(self, diff_text: str) -> list[str]:
        files: list[str] = []
        for line in diff_text.splitlines():
            if line.startswith("diff --git "):
                parts = line.split()
                if len(parts) >= 4:
                    path = parts[3][2:] if parts[3].startswith("b/") else parts[3]
                    if path not in files:
                        files.append(path)
                continue
            if line.startswith("+++ b/"):
                path = line.removeprefix("+++ b/")
                if path != "/dev/null" and path not in files:
                    files.append(path)
        return files

    def _line_counts(self, diff_text: str) -> tuple[int, int]:
        added = 0
        removed = 0
        for line in diff_text.splitlines():
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line.startswith("+"):
                added += 1
            elif line.startswith("-"):
                removed += 1
        return added, removed

    def _observations(self, common_files: list[str], agent_only_files: list[str], human_only_files: list[str]) -> list[str]:
        observations: list[str] = []
        if common_files:
            observations.append(f"Agent 与人工修复共同修改了 {len(common_files)} 个文件，应对比这些文件中的具体逻辑差异。")
        if human_only_files:
            observations.append("人工修复额外修改了 Agent 未覆盖的文件，可能存在漏掉的上下文或测试。")
        if agent_only_files:
            observations.append("Agent 修改了人工修复未涉及的文件，可能存在偏离根因或过度修改。")
        if not common_files and (agent_only_files or human_only_files):
            observations.append("Agent 修复与人工修复没有共同文件，需重点复盘定位方向是否错误。")
        if not observations:
            observations.append("Agent diff 与人工 diff 文件层面一致，可进一步复盘行级策略。")
        return observations
