"""验证 Git 与 Gitee 工具行为。"""

from pathlib import Path

from agent.tools.gitee_tool import GiteeTool
from agent.tools.git_tool import GitTool


def test_git_tool_reports_status(tmp_path: Path) -> None:
    """Git 工具应支持 status 操作。"""
    tool = GitTool(repo_path=str(tmp_path))

    result = tool.run({"action": "status"})

    assert result.tool == "git_tool"
    assert result.exit_code in {0, 128}


def test_gitee_tool_dry_run_create_pull_request() -> None:
    """没有真实 token 时应支持 dry_run 创建 PR。"""
    tool = GiteeTool(owner="demo", repo="auto-fix-agent", dry_run=True)

    result = tool.run({"action": "create_pull_request", "args": {"title": "[Agent Fix] 修复 NPE", "body": "body", "head": "branch", "base": "main"}})

    assert result.success is True
    assert result.data["pr_url"].startswith("dry-run://")


def test_gitee_tool_dry_run_get_pull_request() -> None:
    """dry_run 下应支持查询 PR。"""
    tool = GiteeTool(owner="demo", repo="auto-fix-agent", dry_run=True)

    result = tool.run({"action": "get_pull_request", "args": {"number": 1}})

    assert result.success is True
    assert result.data["pr_url"].endswith("/pulls/1")
