from __future__ import annotations

"""实现最小可运行的修复代理协调逻辑。"""

from agent.config import AgentConfig
from agent.core.permission_guard import PermissionGuard
from agent.core.task_manager import TaskManager
from agent.core.tool_registry import ToolRegistry
from agent.models import TaskStatus
from agent.storage.session_store import SessionStore
from agent.storage.skill_store import SkillStore


class RepairAgent:
    """负责协调任务状态、会话与技能存储的修复代理。"""

    def __init__(
        self,
        config: AgentConfig,
        registry: ToolRegistry,
        permission_guard: PermissionGuard,
        task_manager: TaskManager,
        session_store: SessionStore,
        skill_store: SkillStore,
    ) -> None:
        """初始化修复代理所依赖的各项组件。"""
        self.config = config
        self.registry = registry
        self.permission_guard = permission_guard
        self.task_manager = task_manager
        self.session_store = session_store
        self.skill_store = skill_store

    def repair(self, bug_id: str) -> str:
        """创建并执行一条最小化修复任务流程。"""
        self.task_manager.create_task(bug_id)
        self.task_manager.update_status(bug_id, TaskStatus.RUNNING)
        # 在会话存储中记录当前任务执行状态，便于后续扩展恢复能力。
        self.session_store.put(bug_id, {"state": "repairing"})
        self.task_manager.update_status(bug_id, TaskStatus.PASSED)
        return f"repair task created for {bug_id}"
