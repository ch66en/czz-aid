from __future__ import annotations

"""提供命令执行工具。"""

import subprocess
from pathlib import Path
from typing import Any

from agent.config import AppConfig
from agent.models import ToolResult, ToolSpec
from agent.tools.base import BaseTool, PermissionType


class RunCommandTool(BaseTool):
    """执行 Shell 命令并返回输出结果。"""

    def __init__(self, config: AppConfig | None = None) -> None:
        """初始化命令执行工具。"""
        self.config = config or AppConfig()

    @property
    def spec(self) -> ToolSpec:
        """返回命令执行工具的规格说明。"""
        return ToolSpec(name="run_command", description="Run a shell command", input_schema={"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}, permission=PermissionType.TEST_EXECUTION.value, executor="local")

    @property
    def permission(self) -> PermissionType:
        """返回命令执行工具所需权限。"""
        return PermissionType.TEST_EXECUTION

    def run(self, payload: dict[str, Any] | None = None) -> ToolResult:
        """根据项目配置执行测试或编译命令。"""
        data = payload or {}
        command = str(data.get("command", ""))
        cwd = Path(self.config.project.root) if self.config.project.root else None
        completed = subprocess.run(command, capture_output=True, text=True, shell=True, check=False, cwd=str(cwd) if cwd is not None else None)
        return ToolResult(tool="run_command", success=completed.returncode == 0, exit_code=completed.returncode, stdout_summary=(completed.stdout or "").strip(), stderr_summary=(completed.stderr or "").strip(), data={"command": command}, artifacts=[])
