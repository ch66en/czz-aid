"""验证编译与测试工具的行为。"""

from agent.config import AppConfig
from agent.tools.compile_tool import RunCompileTool
from agent.tools.test_tool import RunTestTool


class FakeRunner:
    """模拟命令执行结果。"""

    def __init__(self, success: bool, exit_code: int, stdout: str, stderr: str) -> None:
        self.success = success
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr

    def run(self, payload: dict[str, str]) -> object:
        class Result:
            pass

        result = Result()
        result.success = self.success
        result.exit_code = self.exit_code
        result.stdout_summary = self.stdout
        result.stderr_summary = self.stderr
        result.artifacts = ["./logs/full.log"]
        return result


def test_compile_tool_uses_project_compile_command() -> None:
    """编译工具应执行项目配置里的编译命令。"""
    config = AppConfig(project={"compile_command": "mvn -q compile"})
    tool = RunCompileTool(config=config, runner=FakeRunner(True, 0, "build ok", ""))

    result = tool.run()

    assert result.success is True
    assert result.data["command"] == "mvn -q compile"
    assert result.stdout_summary == "build ok"


def test_compile_tool_marks_failure_and_summarizes_error() -> None:
    """编译失败时应标记失败并提取错误摘要。"""
    config = AppConfig(project={"compile_command": "mvn compile"})
    tool = RunCompileTool(config=config, runner=FakeRunner(False, 1, "[ERROR] COMPILATION ERROR", "missing import"))

    result = tool.run()

    assert result.success is False
    assert result.exit_code == 1
    assert result.stderr_summary


def test_test_tool_collects_failed_tests_and_reports() -> None:
    """测试工具应尽量解析失败测试与报告路径。"""
    config = AppConfig(project={"test_command": "mvn test"})
    stdout = "Tests run: 2, Failures: 1, Errors: 0, Skipped: 0\nSee /tmp/target/surefire-reports"
    tool = RunTestTool(config=config, runner=FakeRunner(False, 1, stdout, ""))

    result = tool.run()

    assert result.success is False
    assert result.data["command"] == "mvn test"
    assert result.data["failed_tests"]
    assert result.data["surefire_reports"]
