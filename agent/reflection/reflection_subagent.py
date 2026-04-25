from __future__ import annotations

"""实现反思子代理的最小异步前置骨架。"""

from agent.storage.skill_store import SkillStore
from agent.storage.task_store import TaskStore


class ReflectionSubAgent:
    """负责记录修复结果并沉淀反思信息。"""

    def __init__(self, task_store: TaskStore, skill_store: SkillStore) -> None:
        """初始化反思子代理依赖的存储组件。"""
        self.task_store = task_store
        self.skill_store = skill_store

    def reflect(self, bug_id: str, result: str) -> str:
        """根据修复结果记录反思结论。"""
        task = self.task_store.get(bug_id)
        self.skill_store.put(bug_id, f"reflection:{result}")
        status = task.status.value if task is not None else "missing"
        return f"reflection recorded for {bug_id}: result={result}, task_status={status}"
