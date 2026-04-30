from __future__ import annotations

import subprocess

from agent.config import AppConfig
from agent.models import ToolResult
from agent.tools.git_tool import GitTool
from agent.tools.gitee_tool import GiteeTool


class NoNetworkSession:
    def post(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("network should not be called in dry_run")


def test_git_tool_builds_agent_branch_name_in_dry_run() -> None:
    config = AppConfig(project={"name": "order-service", "root": "."})
    tool = GitTool(config)

    result = tool.run(
        {
            "action": "create_branch",
            "args": {
                "bug_id": "BUG-123",
                "short_title": "Null Pointer Error",
                "dry_run": True,
            },
        }
    )

    assert result.success is True
    assert result.data["branch"] == "agent-fix/order-service-bug-123-null-pointer-error"
    assert result.data["dry_run"] is True
    assert "git checkout -B" in result.data["command"]


def test_git_tool_rejects_commit_without_message() -> None:
    result = GitTool().run({"action": "commit", "args": {}})

    assert result.success is False
    assert "message is required" in result.stderr_summary


def test_git_tool_runs_real_branch_diff(tmp_path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=tmp_path, check=True, capture_output=True, text=True)
    source = tmp_path / "Demo.java"
    source.write_text("class Demo { int x = 1; }\n", encoding="utf-8")
    subprocess.run(["git", "add", "Demo.java"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    base_branch = subprocess.run(["git", "branch", "--show-current"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "checkout", "-B", "agent-fix/demo"], cwd=tmp_path, check=True, capture_output=True, text=True)
    source.write_text("class Demo { int x = 2; }\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-am", "fix"], cwd=tmp_path, check=True, capture_output=True, text=True)

    result = GitTool(AppConfig(project={"root": str(tmp_path)})).run({"action": "diff", "args": {"base": base_branch, "target": "agent-fix/demo"}})

    assert result.success is True
    assert "int x = 2" in result.stdout_summary
    assert result.data["changed_files"] == ["Demo.java"]


def test_gitee_tool_creates_complete_pr_dry_run_without_token(monkeypatch) -> None:
    monkeypatch.delenv("GITEE_TOKEN", raising=False)
    monkeypatch.delenv("GITEE_ACCESS_TOKEN", raising=False)
    config = AppConfig(
        project={"name": "order-service", "default_branch": "master"},
        gitee={"owner": "demo", "repo": "order-service"},
    )
    tool = GiteeTool(config, session=NoNetworkSession())  # type: ignore[arg-type]
    compile_result = ToolResult(tool="run_compile", success=True, exit_code=0, stdout_summary="compile ok", stderr_summary="", data={}, artifacts=[])
    test_result = ToolResult(tool="run_test", success=True, exit_code=0, stdout_summary="test ok", stderr_summary="", data={}, artifacts=[])

    result = tool.run(
        {
            "action": "create_pull_request",
            "args": {
                "bug_id": "BUG-1",
                "short_title": "Null Pointer",
                "exception_type": "NullPointerException",
                "message": "user name is null",
                "root_cause": "缺少空值保护。",
                "fix_plan": "补充空值判断并保持原有分支逻辑。",
                "changed_files": ["src/main/java/demo/UserService.java"],
                "compile_result": compile_result,
                "test_result": test_result,
                "risk": "仅影响异常分支。",
                "session_path": "data/sessions/BUG-1",
            },
        }
    )

    assert result.success is True
    assert result.data["dry_run"] is True
    assert result.data["auto_merge"] is False
    assert result.data["head"] == "agent-fix/order-service-bug-1-null-pointer"
    assert result.data["base"] == "master"
    assert result.data["title"] == "[Agent Fix] 修复 NullPointerException"
    for heading in [
        "## Bug 摘要",
        "## 根因分析",
        "## 修复方案",
        "## 修改文件",
        "## mvn compile 结果",
        "## mvn test 结果",
        "## 风险说明",
        "## session 路径",
        "## Review 提醒",
    ]:
        assert heading in result.data["body"]
    assert "Agent 不会自动合并 PR" in result.data["body"]
