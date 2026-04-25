from __future__ import annotations

"""提供修复任务的状态管理能力。"""

from agent.models import RepairTask, TaskStatus
from agent.storage.task_store import TaskStore


class TaskManager:
    """封装修复任务的创建与状态更新流程。"""

    def __init__(self, task_store: TaskStore) -> None:
        """初始化任务管理器。"""
        self.task_store = task_store

    def create_task(self, bug_id: str, project: str = "default-project") -> RepairTask:
        """创建一条新的待处理修复任务。"""
        task = RepairTask(bug_id=bug_id, project=project, status=TaskStatus.PENDING)
        self.task_store.save(task)
        return task

    def update_status(self, bug_id: str, status: TaskStatus) -> RepairTask | None:
        """更新指定缺陷任务的执行状态。"""
        task = self.task_store.get(bug_id)
        if task is None:
            return None
        task.status = status
        self.task_store.save(task)
        return task
