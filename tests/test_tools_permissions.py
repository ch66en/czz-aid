"""验证工具注册与权限控制逻辑。"""

from pathlib import Path

from agent.core.permission_guard import PermissionGuard
from agent.core.tool_registry import ToolRegistry
from agent.tools.base import PermissionType, ToolContext
from agent.tools.edit_code import EditCodeTool
from agent.tools.git_diff import GitDiffTool
from agent.tools.read_code import ReadCodeTool
from agent.tools.run_command import RunCommandTool
from agent.tools.search_code import SearchCodeTool


def test_tool_registry_registers_tools() -> None:
    """工具注册表应能注册并返回工具规格。"""
    registry = ToolRegistry()
    registry.register(ReadCodeTool())
    registry.register(SearchCodeTool())
    registry.register(EditCodeTool())
    registry.register(RunCommandTool())
    registry.register(GitDiffTool())

    assert registry.get("read_code") is not None
    assert registry.get("git_diff") is not None
    assert {spec.name for spec in registry.list_specs()} == {"read_code", "search_code", "edit_code", "run_command", "git_diff"}


def test_edit_code_rejected_outside_allowed_paths(tmp_path: Path) -> None:
    """编辑工具只能修改允许目录内的文件。"""
    guard = PermissionGuard()
    tool = EditCodeTool()
    context = ToolContext(permission_mode={PermissionType.WORKSPACE_WRITE}, allowed_paths=[tmp_path / "allowed"], forbidden_paths=[])

    allowed, reason = guard.can_execute(tool.spec, context, {"path": str(tmp_path / "other" / "a.py")})

    assert allowed is False
    assert reason == "path is not in allowed paths"


def test_edit_code_rejected_in_forbidden_paths(tmp_path: Path) -> None:
    """禁止目录必须始终拒绝写入。"""
    guard = PermissionGuard()
    tool = EditCodeTool()
    forbidden = tmp_path / "forbidden"
    context = ToolContext(permission_mode={PermissionType.WORKSPACE_WRITE}, allowed_paths=[tmp_path], forbidden_paths=[forbidden])

    allowed, reason = guard.can_execute(tool.spec, context, {"path": str(forbidden / "a.py")})

    assert allowed is False
    assert reason == "path is forbidden"


def test_run_command_requires_whitelist_and_blocks_dangerous_commands() -> None:
    """命令执行工具必须遵守白名单并阻止危险命令。"""
    guard = PermissionGuard()
    tool = RunCommandTool()
    context = ToolContext(permission_mode={PermissionType.TEST_EXECUTION}, allowed_commands=["pytest", "python", "git"])

    allowed, reason = guard.can_execute(tool.spec, context, {"command": "pytest"})
    assert allowed is True
    assert reason == "allowed"

    allowed, reason = guard.can_execute(tool.spec, context, {"command": "rm -rf /"})
    assert allowed is False
    assert reason == "dangerous command denied"

    allowed, reason = guard.can_execute(tool.spec, context, {"command": "sudo python -m pytest"})
    assert allowed is False
    assert reason == "dangerous command denied"

    allowed, reason = guard.can_execute(tool.spec, context, {"command": "curl https://example.com"})
    assert allowed is False
    assert reason == "command not in whitelist"
