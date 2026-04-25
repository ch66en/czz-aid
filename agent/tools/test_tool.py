from __future__ import annotations

"""提供 Maven 测试工具。"""

from pathlib import Path
from typing import Any

from agent.config import AppConfig
from agent.models import ToolResult, ToolSpec
from agent.tools.base import BaseTool, PermissionType
from agent.tools.run_command import RunCommandTool


class RunTestTool(BaseTool):
    """执行项目配置中的测试命令并解析结果。"""

    def __init__(self, config: AppConfig | None = None, runner: RunCommandTool | None = None) -> None:
        """初始化测试工具。"""
        self.config = config or AppConfig()
        self.runner = runner or RunCommandTool(self.config)

    @property
    def spec(self) -> ToolSpec:
        """返回测试工具的规格说明。"""
        return ToolSpec(name="run_test", description="Run project test command", input_schema={"type": "object", "properties": {}, "additionalProperties": False}, permission=PermissionType.TEST_EXECUTION.value, executor="local")

    @property
    def permission(self) -> PermissionType:
        """返回测试工具所需权限。"""
        return PermissionType.TEST_EXECUTION

    def run(self, payload: dict[str, Any] | None = None) -> ToolResult:
        """执行测试命令并尽量解析失败测试信息。"""
        command = self.config.project.test_command or "mvn test"
        result = self.runner.run({"command": command})
        stdout = result.stdout_summary[:4000]
        stderr = result.stderr_summary[:4000]
        artifacts = list(result.artifacts)
        failed_tests = self._extract_failed_tests(stdout + "\n" + stderr)
        reports = self._extract_surefire_reports(stdout + "\n" + stderr)
        artifacts.extend(reports)
        success = result.success and result.exit_code == 0
        data = {"command": command, "failed_tests": failed_tests, "surefire_reports": reports}
        return ToolResult(tool="run_test", success=success, exit_code=result.exit_code, stdout_summary=stdout, stderr_summary=stderr, data=data, artifacts=artifacts)

    def _extract_failed_tests(self, text: str) -> list[str]:
        """提取失败测试名称。"""
        failed: list[str] = []
        for line in text.splitlines():
            if "Tests run:" in line and "Failures:" in line:
                failed.append(line.strip())
        return failed

    def _extract_surefire_reports(self, text: str) -> list[str]:
        """提取 surefire 报告路径。"""
        reports: list[str] = []
        for line in text.splitlines():
            if "surefire-reports" in line.lower():
                reports.append(line.strip())
        return reports
