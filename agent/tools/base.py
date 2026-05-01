from __future__ import annotations

"""定义所有工具实现共享的抽象基类与权限上下文。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from agent.models import ToolResult, ToolSpec


class PermissionType(str, Enum):
    """表示工具执行所需的权限类型。"""

    READ_ONLY = "READ_ONLY"
    WORKSPACE_WRITE = "WORKSPACE_WRITE"
    TEST_EXECUTION = "TEST_EXECUTION"
    VCS_WRITE = "VCS_WRITE"
    EXTERNAL_NOTIFY = "EXTERNAL_NOTIFY"


@dataclass(slots=True)
class ToolContext:
    """描述工具执行时的安全上下文。"""

    permission_mode: set[PermissionType] = field(default_factory=lambda: {PermissionType.READ_ONLY})
    allowed_paths: list[Path] = field(default_factory=list)
    forbidden_paths: list[Path] = field(default_factory=list)
    allowed_commands: list[str] = field(default_factory=list)
    allowed_command_patterns: list[str] = field(default_factory=list)


class BaseTool(ABC):
    """约束工具必须暴露规格信息、权限类型与运行入口。"""

    @property
    @abstractmethod
    def spec(self) -> ToolSpec:
        """返回当前工具的元数据描述。"""
        raise NotImplementedError

    @property
    @abstractmethod
    def permission(self) -> PermissionType:
        """返回当前工具所需的权限类型。"""
        raise NotImplementedError

    @abstractmethod
    def run(self, payload: dict[str, Any] | None = None) -> ToolResult:
        """执行工具逻辑并返回统一结果。"""
        raise NotImplementedError
