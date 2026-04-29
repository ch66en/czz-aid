import json
from pathlib import Path
from types import SimpleNamespace

from agent.config import AppConfig
from agent.core.permission_guard import PermissionGuard
from agent.core.repair_agent import RepairAgent
from agent.core.task_manager import TaskManager
from agent.core.tool_registry import ToolRegistry
from agent.models import BugEvent, StackFrame, ToolResult
from agent.storage.session_store import SessionStore
from agent.storage.skill_store import SkillStore


class FakeTool:
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


def test_repair_agent_builds_prompt_template() -> None:
    config = AppConfig()
    agent = RepairAgent(config, ToolRegistry(), PermissionGuard(), TaskManager(task_store=SimpleNamespace(save=lambda *_: None, get=lambda *_: None)), SessionStore(), SkillStore())
    bug_event = BugEvent(bug_id="BUG-1", source="feishu", project="demo", title="t", exception_type="E", message="m", traceback="full stack", fingerprint="fp")

    prompt = agent.build_prompt_template(bug_event, ["skill-a"], {"state": "x", "frame_contexts": [{"filePath": "Demo.java"}]})
    payload = json.loads(prompt)

    assert "compile and test" in prompt
    assert "project-scoped Java keyword searches" in prompt
    assert "finish_patch" in prompt
    assert "BUG-1" in prompt
    assert "traceback" not in payload["bug_event"]
    assert payload["bug_event"]["traceback_omitted"] is True
    assert payload["frame_contexts"] == [{"filePath": "Demo.java"}]
    assert "frame_contexts" not in payload["session"]


def test_repair_agent_defaults_search_code_root_to_bug_project_root(tmp_path: Path) -> None:
    config = AppConfig()
    config.project.name = "mall-service"
    config.project.root = str(tmp_path)
    agent = RepairAgent(config, ToolRegistry(), PermissionGuard(), TaskManager(task_store=SimpleNamespace(save=lambda *_: None, get=lambda *_: None)), SessionStore(), SkillStore())
    bug_event = BugEvent(bug_id="BUG-1", source="log", project="mall-service", title="t", exception_type="E", message="m", fingerprint="fp")

    arguments = agent._prepare_tool_arguments("search_code", {"root": ".", "keyword": "ExceptionHandler"}, bug_event)

    assert arguments["root"] == str(tmp_path)
    assert arguments["keyword"] == "ExceptionHandler"


def test_repair_agent_normalizes_list_llm_output_to_single_action() -> None:
    agent = RepairAgent(AppConfig(), ToolRegistry(), PermissionGuard(), TaskManager(task_store=SimpleNamespace(save=lambda *_: None, get=lambda *_: None)), SessionStore(), SkillStore())

    action = agent._normalize_llm_action([
        {"tool": "search_code", "arguments": {"keyword": "ExceptionHandler"}, "reason": "find handler"}
    ])

    assert action == {"tool": "search_code", "arguments": {"keyword": "ExceptionHandler"}, "reason": "find handler"}


def test_repair_agent_requires_compile_and_test_after_finish_patch(tmp_path: Path) -> None:
    source = tmp_path / "QuickSortWithBugLogFile.java"
    source.write_text("class QuickSortWithBugLogFile { void p() { int pivot = arr[right + 1]; } }", encoding="utf-8")
    config = AppConfig()
    config.project.root = str(tmp_path)
    registry = ToolRegistry()
    registry.register(FakeTool("run_compile", "TEST_EXECUTION", ToolResult(tool="run_compile", success=True, exit_code=0, stdout_summary="compile ok", stderr_summary="", data={}, artifacts=[])))
    registry.register(FakeTool("run_test", "TEST_EXECUTION", ToolResult(tool="run_test", success=True, exit_code=0, stdout_summary="test ok", stderr_summary="", data={"failed_tests": [], "surefire_reports": []}, artifacts=[])))
    registry.register(FakeTool("edit_code", "WORKSPACE_WRITE", ToolResult(tool="edit_code", success=True, exit_code=0, stdout_summary="patched", stderr_summary="", data={"path": str(source)}, artifacts=[str(source)])))

    session_store = SessionStore()
    session_store.put(
        "bug_event:BUG-1",
        BugEvent(
            bug_id="BUG-1",
            source="log:test",
            project="demo",
            title="Array index",
            exception_type="ArrayIndexOutOfBoundsException",
            message="Index 7 out of bounds",
            traceback="QuickSortWithBugLogFile.java:1",
            frames=[StackFrame(file_path="QuickSortWithBugLogFile.java", function_name="p", line_number=1)],
            fingerprint="fp",
        ).model_dump(),
    )
    session_store.put("BUG-1", {"frame_contexts": [{"filePath": str(source)}]})
    agent = RepairAgent(config, registry, PermissionGuard(), TaskManager(task_store=SimpleNamespace(save=lambda *_: None, get=lambda *_: None)), session_store, SkillStore())
    agent._create_pr = lambda task, bug_event, history: ToolResult(tool="create_pr", success=True, exit_code=0, stdout_summary="https://gitee.test/pr/1", stderr_summary="", data={"pr_url": "https://gitee.test/pr/1"}, artifacts=[])

    result = agent.repair("BUG-1")

    assert result.success is True
    assert result.last_result is not None
    assert result.last_result.tool == "run_test"
    assert session_store.get("pr:") is None


def test_repair_agent_builds_patch_from_current_source_spacing(tmp_path: Path) -> None:
    source = tmp_path / "QuickSortWithBugLogFile.java"
    source.write_text(
        "class QuickSortWithBugLogFile {\n"
        "    void p() {\n"
        "        int pivot = arr[right+1];\n"
        "        log(\"pivot = \" + pivot + \", position: \" + (right + 1));\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    agent = RepairAgent(AppConfig(), ToolRegistry(), PermissionGuard(), TaskManager(task_store=SimpleNamespace(save=lambda *_: None, get=lambda *_: None)), SessionStore(), SkillStore())

    patch = agent._build_patch_content(str(source))

    assert "-        int pivot = arr[right+1];" in patch
    assert "+        int pivot = arr[right];" in patch
    assert "position: \" + right" in patch


def test_repair_agent_parses_gitee_remote_url() -> None:
    agent = RepairAgent(AppConfig(), ToolRegistry(), PermissionGuard(), TaskManager(task_store=SimpleNamespace(save=lambda *_: None, get=lambda *_: None)), SessionStore(), SkillStore())

    assert agent._parse_gitee_remote("https://gitee.com/ch6enle/agent_test_1.git") == ("ch6enle", "agent_test_1")
    assert agent._parse_gitee_remote("git@gitee.com:ch6enle/agent_test_1.git") == ("ch6enle", "agent_test_1")


def test_repair_agent_create_pr_requires_real_token(tmp_path: Path) -> None:
    source = tmp_path / "Demo.java"
    source.write_text("class Demo {}\n", encoding="utf-8")
    config = AppConfig()
    config.project.root = str(tmp_path)
    config.gitee.owner = "ch6enle"
    config.gitee.repo = "agent_test_1"
    config.gitee.token = "your-gitee-token"
    agent = RepairAgent(config, ToolRegistry(), PermissionGuard(), TaskManager(task_store=SimpleNamespace(save=lambda *_: None, get=lambda *_: None)), SessionStore(), SkillStore())
    history = [{"tool": "edit_code", "result": {"success": True, "artifacts": [str(source)], "data": {"path": str(source)}}}]
    bug_event = BugEvent(bug_id="BUG-PR", source="log", project="demo", title="", exception_type="E", message="", fingerprint="fp")

    result = agent._create_pr(SimpleNamespace(id="task-1", bug_id="BUG-PR"), bug_event, history)

    assert result.success is False
    assert result.stderr_summary == "missing gitee token"
