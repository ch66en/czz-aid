from __future__ import annotations

"""提供会话数据读写工具。"""

from typing import Any

from agent.models import ToolResult, ToolSpec
from agent.storage.session_store import SessionStore
from agent.tools.base import BaseTool


class SessionTool(BaseTool):
    """基于会话存储提供统一读写接口。"""

    def __init__(self, session_store: SessionStore) -> None:
        """初始化会话工具所依赖的存储对象。"""
        self.session_store = session_store

    @property
    def spec(self) -> ToolSpec:
        """返回会话工具的规格说明。"""
        return ToolSpec(name="session_tool", description="Read and write session data")

    def run(self, payload: dict[str, Any] | None = None) -> ToolResult:
        """根据是否提供值决定读取或写入会话数据。"""
        data = payload or {}
        key = str(data.get("key", ""))
        value = data.get("value")
        if value is None:
            return ToolResult(
                tool="session_tool",
                success=True,
                exit_code=0,
                stdout_summary=str(self.session_store.get(key)),
                stderr_summary="",
                data={"key": key},
                artifacts=[],
            )
        self.session_store.put(key, value)
        return ToolResult(
            tool="session_tool",
            success=True,
            exit_code=0,
            stdout_summary=key,
            stderr_summary="",
            data={"key": key},
            artifacts=[],
        )
