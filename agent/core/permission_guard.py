from __future__ import annotations

"""定义工具权限检查逻辑。"""

import re
import shlex
from pathlib import Path
from typing import Any

from agent.config import AppConfig
from agent.models import ToolSpec
from agent.tools.base import PermissionType, ToolContext

_EDIT_FORBIDDEN_DIRS = {".git", ".github", ".gitee"}


class PermissionGuard:
    """负责判断工具是否可以在当前阶段直接执行。"""

    _FORBIDDEN_COMMAND_TOKENS = {"rm", "sudo", "chmod", "ssh", "scp", "del", "erase", "rmdir", "curl", "wget", "nc", "ncat"}
    _FORBIDDEN_COMMAND_PATTERNS = [
        re.compile(r"(?:^|\s)(?:rm\s+-rf|rm\s+-fr|del\b|erase\b|rmdir\b|sudo\b|chmod\b)", re.IGNORECASE),
        re.compile(r"\|\s*(?:bash|sh|powershell|cmd\.exe)\b", re.IGNORECASE),
        re.compile(r"(?:^|\s)(?:git\s+reset\s+--hard|git\s+push\s+--force|git\s+clean\s+-fdx)", re.IGNORECASE),
        re.compile(r"(?:^|\s)python\w?\s+.*-c\b", re.IGNORECASE),
        re.compile(r"(?:^|\s)(?:curl|wget)\b", re.IGNORECASE),
        re.compile(r"(?:^|\s)(?:nc|ncat)\b", re.IGNORECASE),
    ]

    def __init__(self, config: AppConfig | None = None) -> None:
        self._config = config

    def build_context(self, permission: PermissionType) -> ToolContext:
        """根据配置和权限类型构建 ToolContext。"""
        config = self._config
        if config is None:
            return ToolContext(permission_mode={permission})

        allowed_paths: list[Path] = []
        forbidden_paths: list[Path] = []
        allowed_commands: list[str] = []

        if permission == PermissionType.WORKSPACE_WRITE:
            project_root = Path(config.project.root).resolve()
            allowed_paths = [project_root]
            forbidden_paths = [project_root / d for d in _EDIT_FORBIDDEN_DIRS]

        if permission == PermissionType.TEST_EXECUTION:
            allowed_commands = list(config.project.allowed_commands)

        return ToolContext(
            permission_mode={permission},
            allowed_paths=allowed_paths,
            forbidden_paths=forbidden_paths,
            allowed_commands=allowed_commands,
        )

    def is_allowed(self, spec: ToolSpec) -> bool:
        """根据权限类型判断工具是否属于默认可执行。"""
        try:
            permission = PermissionType(spec.permission)
        except ValueError:
            return False
        return permission in {PermissionType.READ_ONLY, PermissionType.TEST_EXECUTION}

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
        raw = str(payload.get("path", ""))
        target_path = Path(raw).expanduser()
        if not target_path.is_absolute() and self._config is not None:
            target_path = Path(self._config.project.root).expanduser() / target_path
        target = target_path.resolve()
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

        normalized_command = command.lower()
        if any(pattern.search(command) for pattern in self._FORBIDDEN_COMMAND_PATTERNS):
            return False, "dangerous command denied"

        lower_tokens = {token.lower() for token in tokens}
        if lower_tokens & self._FORBIDDEN_COMMAND_TOKENS:
            return False, "dangerous command denied"

        if context.allowed_command_patterns and not any(re.fullmatch(pattern, command, flags=re.IGNORECASE) for pattern in context.allowed_command_patterns):
            return False, "command not in whitelist"

        if context.allowed_commands:
            allowed = {cmd.lower() for cmd in context.allowed_commands}
            if tokens[0].lower() not in allowed:
                return False, "command not in whitelist"
            return True, "allowed"

        if normalized_command.startswith("mvn ") or normalized_command == "mvn":
            return True, "allowed"

        return False, "command not in whitelist"

    def _is_under(self, target: Path, root: Path) -> bool:
        """判断目标路径是否位于某个根路径之下。"""
        try:
            target.relative_to(root.resolve())
            return True
        except ValueError:
            return False
