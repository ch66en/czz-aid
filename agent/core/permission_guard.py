from __future__ import annotations

"""定义工具权限检查逻辑。"""

from agent.models import ToolSpec


class PermissionGuard:
    """负责判断工具是否可以在当前阶段直接执行。"""

    def is_allowed(self, spec: ToolSpec) -> bool:
        """根据工具声明判断是否无需额外审批。"""
        return not spec.requires_approval
