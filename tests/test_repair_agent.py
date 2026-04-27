"""验证修复代理运行时的粗流程约束。"""

from types import SimpleNamespace

from agent.config import AppConfig
from agent.core.permission_guard import PermissionGuard
from agent.core.repair_agent import RepairAgent
from agent.core.task_manager import TaskManager
from agent.core.tool_registry import ToolRegistry
from agent.models import BugEvent, ToolResult
from agent.storage.session_store import SessionStore
from agent.storage.skill_store import SkillStore


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


class FakeLLM:
    """Return scripted JSON actions."""

    def __init__(self, actions: list[str]) -> None:
        self.actions = actions
        self.calls = 0

    def chat(self, messages: list[dict[str, object]]) -> ToolResult:
        payload = self.actions[min(self.calls, len(self.actions) - 1)]
        self.calls += 1
        return ToolResult(tool="llm_chat", success=True, exit_code=0, stdout_summary=payload, stderr_summary="", data={}, artifacts=[])


def test_repair_agent_builds_prompt_template() -> None:
    """提示词应包含硬约束与上下文。"""
    config = AppConfig()
    agent = RepairAgent(config, ToolRegistry(), PermissionGuard(), TaskManager(task_store=SimpleNamespace(save=lambda *_: None, get=lambda *_: None)), SessionStore(), SkillStore())
    bug_event = BugEvent(bug_id="BUG-1", source="feishu", project="demo", title="t", exception_type="E", message="m", fingerprint="fp")

    prompt = agent.build_prompt_template(bug_event, ["skill-a"], {"state": "x"})

    assert "禁止跳过 compile" in prompt
    assert "finish_patch" in prompt
    assert "BUG-1" in prompt


def test_repair_agent_requires_compile_and_test_after_finish_patch() -> None:
    """finish_patch 后必须强制执行编译和测试。"""
    config = AppConfig()
    registry = ToolRegistry()
    registry.register(FakeTool("run_compile", "TEST_EXECUTION", ToolResult(tool="run_compile", success=True, exit_code=0, stdout_summary="compile ok", stderr_summary="", data={}, artifacts=[])))
    registry.register(FakeTool("run_test", "TEST_EXECUTION", ToolResult(tool="run_test", success=True, exit_code=0, stdout_summary="test ok", stderr_summary="", data={"failed_tests": [], "surefire_reports": []}, artifacts=[])))
    registry.register(FakeTool("edit_code", "WORKSPACE_WRITE", ToolResult(tool="edit_code", success=True, exit_code=0, stdout_summary="patched", stderr_summary="", data={}, artifacts=[])))

    session_store = SessionStore()
    skill_store = SkillStore()
    task_store = SimpleNamespace(save=lambda *_: None, get=lambda *_: None)
    llm = FakeLLM(
        [
            '{"tool":"edit_code","arguments":{"path":"src/main/java/Demo.java","content":"--- a/src/main/java/Demo.java\\n+++ b/src/main/java/Demo.java\\n@@\\n- bad();\\n+ good();"},"reason":"patch"}',
            '{"tool":"finish_patch","arguments":{},"reason":"done"}',
        ]
    )
    agent = RepairAgent(config, registry, PermissionGuard(), TaskManager(task_store=task_store), session_store, skill_store, llm_client=llm)

    result = agent.repair("BUG-1")

    assert result.success is True
    assert result.last_result is not None
    assert result.last_result.tool == "run_test"
    assert session_store.get("pr:") is None


def test_repair_agent_reads_llm_stdout_summary_and_normalizes_tool_name(capsys) -> None:
    """OpenAI-compatible fallback output should still drive registered snake_case tools."""
    config = AppConfig()
    payload = '{"tool":"SearchCode","arguments":{"keyword":"Demo"},"reason":"search"}'
    agent = RepairAgent(config, ToolRegistry(), PermissionGuard(), TaskManager(task_store=SimpleNamespace(save=lambda *_: None, get=lambda *_: None)), SessionStore(), SkillStore(), llm_client=FakeLLM([payload]))

    action = agent._ask_llm("prompt", [])
    output = capsys.readouterr().out

    assert action["tool"] == "search_code"
    assert action["arguments"] == {"keyword": "Demo"}
    assert "[llm] output:" in output
    assert payload in output


def test_repair_agent_without_llm_does_not_emit_demo_patch() -> None:
    """No LLM should fail closed instead of applying a hard-coded demo patch."""
    config = AppConfig()
    agent = RepairAgent(config, ToolRegistry(), PermissionGuard(), TaskManager(task_store=SimpleNamespace(save=lambda *_: None, get=lambda *_: None)), SessionStore(), SkillStore())

    action = agent._ask_llm("QuickSortWithBugLogFile.java", [])

    assert action["tool"] == "finish_patch"
    assert "llm client missing" in action["reason"]
