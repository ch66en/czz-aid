from __future__ import annotations

"""提供命令执行工具。"""

import subprocess
from typing import Any

from agent.models import ToolResult, ToolSpec
from agent.tools.base import BaseTool


class RunCommandTool(BaseTool):
    """执行 Shell 命令并返回输出结果。"""

    @property
    def spec(self) -> ToolSpec:
        """返回命令执行工具的规格说明。"""
        return ToolSpec(name="run_command", description="Run a shell command", requires_approval=True)

    def run(self, payload: dict[str, Any] | None = None) -> ToolResult:
        """根据传入命令执行子进程。"""
        data = payload or {}
        command = str(data.get("command", ""))
        completed = subprocess.run(command, capture_output=True, text=True, shell=True, check=False)
        return ToolResult(
            tool="run_command",
            success=completed.returncode == 0,
            exit_code=completed.returncode,
            stdout_summary=(completed.stdout or "").strip(),
            stderr_summary=(completed.stderr or "").strip(),
            data={"command": command},
            artifacts=[],
        )
