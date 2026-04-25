from __future__ import annotations

"""定义工具权限检查逻辑。"""

import shlex
from pathlib import Path
from typing import Any

from agent.models import ToolSpec
from agent.tools.base import PermissionType, ToolContext


class PermissionGuard:
    """负责判断工具是否可以在当前阶段直接执行。"""

    _FORBIDDEN_COMMAND_TOKENS = {"rm", "sudo", "chmod", "ssh", "scp"}

    def is_allowed(self, spec: ToolSpec) -> bool:
        """根据工具声明判断是否无需额外审批。"""
        return not spec.requires_approval

    def can_execute(self, spec: ToolSpec, context: ToolContext, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        """检查当前上下文是否允许执行指定工具。"""
        if PermissionType(spec.permission) not in context.permission_mode:
            return False, "permission mode denied"

        payload = payload or {}
        if spec.name == "edit_code":
            return self._check_edit_code(context, payload)
        if spec.name == "run_command":
            return self._check_run_command(context, payload)
        return True, "allowed"

    def _check_edit_code(self, context: ToolContext, payload: dict[str, Any]) -> tuple[bool, str]:
        """检查文件写入是否落在允许路径内。"""
        target = Path(str(payload.get("path", ""))).resolve()
        if any(self._is_under(target, forbidden) for forbidden in context.forbidden_paths):
            return False, "path is forbidden"
        if context.allowed_paths and not any(self._is_under(target, allowed) for allowed in context.allowed_paths):
            return False, "path is not in allowed paths"
        return True, "allowed"

    def _check_run_command(self, context: ToolContext, payload: dict[str, Any]) -> tuple[bool, str]:
        """检查命令是否在白名单内且不包含危险操作。"""
        command = str(payload.get("command", "")).strip()
        if not command:
            return False, "empty command"
        tokens = shlex.split(command, posix=False)
        if not tokens:
            return False, "empty command"
        lower_tokens = {token.lower() for token in tokens}
        if lower_tokens & self._FORBIDDEN_COMMAND_TOKENS:
            return False, "dangerous command denied"
        if context.allowed_commands and tokens[0] not in context.allowed_commands:
            return False, "command not in whitelist"
        return True, "allowed"

    def _is_under(self, target: Path, root: Path) -> bool:
        """判断目标路径是否位于某个根路径之下。"""
        try:
            target.relative_to(root.resolve())
            return True
        except ValueError:
            return False
