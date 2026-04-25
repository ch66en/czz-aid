"""验证修复完成后的 PR 拼装逻辑。"""

from types import SimpleNamespace

from agent.config import AppConfig
from agent.core.permission_guard import PermissionGuard
from agent.core.repair_agent import RepairAgent
from agent.core.task_manager import TaskManager
from agent.core.tool_registry import ToolRegistry
from agent.models import BugEvent, RepairTask, ToolResult
from agent.storage.session_store import SessionStore
from agent.storage.skill_store import SkillStore
from agent.tools.gitee_tool import GiteeTool
from agent.tools.git_tool import GitTool


class FakeTool:
    """模拟工具对象。"""

    def __init__(self, name: str, permission: str, result: ToolResult) -> None:
        self._spec = SimpleNamespace(name=name, permission=permission, description=name, input_schema={}, executor="local")
        self._result = result

    @property
    def spec(self) -> SimpleNamespace:
        return self._spec

    @property
    def permission(self) -> str:
        return self._spec.permission

    def run(self, payload: dict[str, object] | None = None) -> ToolResult:
        return self._result


def test_repair_agent_builds_branch_and_pr_body() -> None:
    """应生成符合规则的分支名、PR 标题与描述。"""
    config = AppConfig(project={"name": "book-service", "default_branch": "main"}, gitee={"owner": "demo", "repo": "auto-fix-agent", "base_url": "https://gitee.com/api/v5"})
    registry = ToolRegistry()
    agent = RepairAgent(config, registry, PermissionGuard(), TaskManager(task_store=SimpleNamespace(save=lambda *_: None, get=lambda *_: None)), SessionStore(), SkillStore())
    bug_event = BugEvent(bug_id="BUG-1", source="feishu", project="book-service", title="Borrow Service NPE", exception_type="NullPointerException", message="Cannot invoke x", request_path="/api/books/1", top_business_frame="com.example.book.service.BorrowService.borrowBook", fingerprint="fp")
    task = RepairTask(bug_id="BUG-1", project="book-service", session_path="/tmp/session")

    branch = agent._build_branch_name(bug_event)
    body = agent._build_pr_body(task, bug_event)

    assert branch.startswith("agent-fix/book-service-BUG-1-")
    assert "Bug 摘要" in body
    assert "根因分析" in body
    assert "修复方案" in body
    assert "修改文件" in body
    assert "mvn compile 结果" in body
    assert "mvn test 结果" in body
    assert "Review 提醒" in body
    assert "/tmp/session" in body


def test_repair_agent_success_flow_uses_git_and_gitee_tools(tmp_path) -> None:
    """成功后应进入 Git + Gitee PR 流程。"""
    config = AppConfig(project={"name": "book-service", "default_branch": "main"}, workspace=str(tmp_path), gitee={"owner": "demo", "repo": "auto-fix-agent", "base_url": "https://gitee.com/api/v5"})
    registry = ToolRegistry()
    registry.register(GitTool(repo_path=str(tmp_path)))
    registry.register(GiteeTool(owner="demo", repo="auto-fix-agent", dry_run=True))
    registry.register(FakeTool("run_compile", "TEST_EXECUTION", ToolResult(tool="run_compile", success=True, exit_code=0, stdout_summary="compile ok", stderr_summary="", data={}, artifacts=[])))
    registry.register(FakeTool("run_test", "TEST_EXECUTION", ToolResult(tool="run_test", success=True, exit_code=0, stdout_summary="test ok", stderr_summary="", data={"failed_tests": [], "surefire_reports": []}, artifacts=[])))
    registry.register(FakeTool("edit_code", "WORKSPACE_WRITE", ToolResult(tool="edit_code", success=True, exit_code=0, stdout_summary="patched", stderr_summary="", data={}, artifacts=[])))

    session_store = SessionStore()
    session_store.put("BUG-1", {"modified_files": ["src/Main.java"], "compile_result": {"stdout_summary": "compile ok"}, "test_result": {"stdout_summary": "test ok"}})
    skill_store = SkillStore()
    task_store = SimpleNamespace(save=lambda *_: None, get=lambda *_: None)
    agent = RepairAgent(config, registry, PermissionGuard(), TaskManager(task_store=task_store), session_store, skill_store)

    result = agent._create_pr(RepairTask(bug_id="BUG-1", project="book-service"), BugEvent(bug_id="BUG-1", source="feishu", project="book-service", title="Borrow Service NPE", exception_type="NullPointerException", message="Cannot invoke x", request_path="/api/books/1", top_business_frame="com.example.book.service.BorrowService.borrowBook", fingerprint="fp"))

    assert result.success is True
    assert result.data["pr_url"].startswith("dry-run://")
