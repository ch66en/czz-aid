"""验证回滚与 Git 跟踪检查功能。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from agent.config import AppConfig
from agent.core.repair_agent import RepairAgent
from agent.core.task_manager import TaskManager
from agent.core.permission_guard import PermissionGuard
from agent.core.tool_registry import ToolRegistry
from agent.models import BugEvent, ToolResult
from agent.storage.session_store import SessionStore
from agent.storage.skill_store import SkillStore


def _init_git_repo(tmp_path: Path) -> None:
    """在临时目录初始化一个带初始提交的 Git 仓库。"""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=tmp_path, check=True, capture_output=True, text=True)


def _make_agent(tmp_path: Path) -> RepairAgent:
    config = AppConfig()
    config.project.root = str(tmp_path)
    config.project.name = "test-project"
    return RepairAgent(
        config,
        ToolRegistry(),
        PermissionGuard(),
        TaskManager(task_store=SimpleNamespace(save=lambda *_: None, get=lambda *_: None)),
        SessionStore(),
        SkillStore(),
    )


def _make_bug_event() -> BugEvent:
    return BugEvent(bug_id="BUG-TEST", source="log", project="test-project", title="t", exception_type="E", message="m", fingerprint="fp")


def test_is_git_tracked_returns_true_for_tracked_file(tmp_path: Path) -> None:
    """已被 git add 的文件应返回 True。"""
    _init_git_repo(tmp_path)
    source = tmp_path / "Demo.java"
    source.write_text("class Demo {}\n", encoding="utf-8")
    subprocess.run(["git", "add", "Demo.java"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)

    agent = _make_agent(tmp_path)
    assert agent._is_git_tracked(source, tmp_path) is True


def test_is_git_tracked_returns_false_for_untracked_file(tmp_path: Path) -> None:
    """未 git add 的文件应返回 False。"""
    _init_git_repo(tmp_path)
    source = tmp_path / "Untracked.java"
    source.write_text("class Untracked {}\n", encoding="utf-8")

    agent = _make_agent(tmp_path)
    assert agent._is_git_tracked(source, tmp_path) is False


def test_rollback_restores_tracked_file_to_original_content(tmp_path: Path) -> None:
    """回滚应将已跟踪文件恢复到 Git 中的版本。"""
    _init_git_repo(tmp_path)
    source = tmp_path / "Service.java"
    original = "class Service { int x = 1; }\n"
    source.write_text(original, encoding="utf-8")
    subprocess.run(["git", "add", "Service.java"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)

    source.write_text("class Service { int x = 999; }\n", encoding="utf-8")

    agent = _make_agent(tmp_path)
    bug_event = _make_bug_event()
    history = [
        {
            "tool": "edit_code",
            "result": {
                "tool": "edit_code",
                "success": True,
                "exit_code": 0,
                "stdout_summary": str(source),
                "stderr_summary": "",
                "data": {"path": str(source)},
                "artifacts": [str(source)],
            },
        }
    ]

    result = agent._rollback_git_changes(history, bug_event)

    assert result.success is True
    assert source.read_text(encoding="utf-8") == original
    assert "restored 1 tracked file" in result.stdout_summary
    assert str(source) in [str(p) for p in result.data["restored"]]


def test_rollback_removes_untracked_new_file(tmp_path: Path) -> None:
    """回滚应删除本轮新增的未跟踪文件。"""
    _init_git_repo(tmp_path)
    existing = tmp_path / "Existing.java"
    existing.write_text("class Existing {}\n", encoding="utf-8")
    subprocess.run(["git", "add", "Existing.java"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)

    new_file = tmp_path / "AgentCreated.java"
    new_file.write_text("class AgentCreated {}\n", encoding="utf-8")

    agent = _make_agent(tmp_path)
    bug_event = _make_bug_event()
    history = [
        {
            "tool": "edit_code",
            "result": {
                "tool": "edit_code",
                "success": True,
                "exit_code": 0,
                "stdout_summary": str(new_file),
                "stderr_summary": "",
                "data": {"path": str(new_file)},
                "artifacts": [str(new_file)],
            },
        }
    ]

    result = agent._rollback_git_changes(history, bug_event)

    assert result.success is True
    assert not new_file.exists()
    assert "removed 1 untracked file" in result.stdout_summary


def test_rollback_handles_mixed_tracked_and_untracked(tmp_path: Path) -> None:
    """回滚应同时恢复已跟踪文件并删除未跟踪文件。"""
    _init_git_repo(tmp_path)
    tracked = tmp_path / "Tracked.java"
    tracked.write_text("class Tracked { int v = 1; }\n", encoding="utf-8")
    subprocess.run(["git", "add", "Tracked.java"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)

    tracked.write_text("class Tracked { int v = 999; }\n", encoding="utf-8")
    untracked = tmp_path / "NewFile.java"
    untracked.write_text("class NewFile {}\n", encoding="utf-8")

    agent = _make_agent(tmp_path)
    bug_event = _make_bug_event()
    history = [
        {
            "tool": "edit_code",
            "result": {
                "tool": "edit_code",
                "success": True,
                "exit_code": 0,
                "stdout_summary": str(tracked),
                "stderr_summary": "",
                "data": {"path": str(tracked)},
                "artifacts": [str(tracked)],
            },
        },
        {
            "tool": "edit_code",
            "result": {
                "tool": "edit_code",
                "success": True,
                "exit_code": 0,
                "stdout_summary": str(untracked),
                "stderr_summary": "",
                "data": {"path": str(untracked)},
                "artifacts": [str(untracked)],
            },
        },
    ]

    result = agent._rollback_git_changes(history, bug_event)

    assert result.success is True
    assert tracked.read_text(encoding="utf-8") == "class Tracked { int v = 1; }\n"
    assert not untracked.exists()
    assert len(result.data["restored"]) == 1
    assert len(result.data["removed"]) == 1


def test_rollback_returns_success_when_no_files_edited(tmp_path: Path) -> None:
    """没有编辑记录时回滚应直接返回成功。"""
    _init_git_repo(tmp_path)
    agent = _make_agent(tmp_path)
    bug_event = _make_bug_event()

    result = agent._rollback_git_changes([], bug_event)

    assert result.success is True
    assert "no files to rollback" in result.stdout_summary
