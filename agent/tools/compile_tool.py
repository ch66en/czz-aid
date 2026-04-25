from __future__ import annotations

"""提供 Maven 编译工具。"""

from pathlib import Path
from typing import Any

from agent.config import AppConfig
from agent.models import ToolResult, ToolSpec
from agent.tools.base import BaseTool, PermissionType
from agent.tools.run_command import RunCommandTool


class RunCompileTool(BaseTool):
    """执行项目配置中的编译命令并摘要错误信息。"""

    def __init__(self, config: AppConfig | None = None, runner: RunCommandTool | None = None) -> None:
        """初始化编译工具。"""
        self.config = config or AppConfig()
        self.runner = runner or RunCommandTool(self.config)

    @property
    def spec(self) -> ToolSpec:
        """返回编译工具的规格说明。"""
        return ToolSpec(name="run_compile", description="Run project compile command", input_schema={"type": "object", "properties": {}, "additionalProperties": False}, permission=PermissionType.TEST_EXECUTION.value, executor="local")

    @property
    def permission(self) -> PermissionType:
        """返回编译工具所需权限。"""
        return PermissionType.TEST_EXECUTION

    def run(self, payload: dict[str, Any] | None = None) -> ToolResult:
        """执行编译命令并返回摘要。"""
        command = self.config.project.compile_command or "mvn compile"
        result = self.runner.run({"command": command})
        stdout = result.stdout_summary[:4000]
        stderr = result.stderr_summary[:4000]
        artifacts = list(result.artifacts)
        if payload and payload.get("log_path"):
            artifacts.append(str(Path(str(payload["log_path"]))))
        success = result.success and result.exit_code == 0
        if not success:
            stderr = stderr or self._extract_compile_error(stdout + "\n" + stderr)
        return ToolResult(tool="run_compile", success=success, exit_code=result.exit_code, stdout_summary=stdout, stderr_summary=stderr, data={"command": command}, artifacts=artifacts)

    def _extract_compile_error(self, text: str) -> str:
        """提取编译失败的摘要信息。"""
        markers = ["COMPILATION ERROR", "cannot find symbol", "package does not exist", "unreported exception", "incompatible types", "method ", "Failed to execute goal"]
        for marker in markers:
            if marker.lower() in text.lower():
                return marker
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return lines[-1][:500] if lines else "compile failed"
