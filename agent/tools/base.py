from __future__ import annotations

"""定义所有工具实现共享的抽象基类。"""

from abc import ABC, abstractmethod
from typing import Any

from agent.models import ToolResult, ToolSpec


class BaseTool(ABC):
    """约束工具必须暴露规格信息与运行入口。"""

    @property
    @abstractmethod
    def spec(self) -> ToolSpec:
        """返回当前工具的元数据描述。"""
        raise NotImplementedError

    @abstractmethod
    def run(self, payload: dict[str, Any] | None = None) -> ToolResult:
        """执行工具逻辑并返回统一结果。"""
        raise NotImplementedError
