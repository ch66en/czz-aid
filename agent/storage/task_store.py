from __future__ import annotations

"""提供修复任务的简单存储实现。"""

from agent.models import RepairTask


class TaskStore:
    """以内存字典形式存取修复任务。"""

    def __init__(self) -> None:
        """初始化空的任务存储。"""
        self._items: dict[str, RepairTask] = {}

    def get(self, bug_id: str) -> RepairTask | None:
        """根据缺陷编号读取任务对象。"""
        return self._items.get(bug_id)

    def save(self, task: RepairTask) -> None:
        """保存或覆盖指定缺陷编号对应的任务。"""
        self._items[task.bug_id] = task
