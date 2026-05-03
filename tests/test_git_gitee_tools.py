from __future__ import annotations

import subprocess

from agent.config import AppConfig
from agent.tools.git_tool import GitTool


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
