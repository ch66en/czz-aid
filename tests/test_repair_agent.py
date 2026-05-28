import json
from pathlib import Path
from types import SimpleNamespace

from agent.config import AppConfig
from agent.core.permission_guard import PermissionGuard
from agent.core.repair_agent import RepairAgent
from agent.core.task_manager import TaskManager
from agent.core.tool_registry import ToolRegistry
from agent.models import BugEvent, RepairTask, StackFrame, ToolResult
from agent.rag.models import RetrievalResult
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


class RecordingFeishuTool:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []
        self._spec = SimpleNamespace(name="feishu_tool", permission="EXTERNAL_NOTIFY", description="feishu", input_schema={}, executor="local")

    @property
    def spec(self) -> SimpleNamespace:
        return self._spec

    @property
    def permission(self) -> str:
        return self._spec.permission

    def run(self, payload: dict[str, object] | None = None) -> ToolResult:
        self.payloads.append(payload or {})
        return ToolResult(tool="feishu_tool", success=True, exit_code=0, stdout_summary="sent", stderr_summary="", data={"dry_run": False}, artifacts=[])


class NativeToolCallLLM:
    def __init__(self) -> None:
        self.last_messages: list[dict[str, object]] | None = None
        self.last_tools: list[dict[str, object]] | None = None
        self.last_tool_choice: object = None

    def chat(self, messages, tools=None, tool_choice=None):
        self.last_messages = messages
        self.last_tools = tools
        self.last_tool_choice = tool_choice
        return ToolResult(
            tool="llm_chat",
            success=True,
            exit_code=0,
            data={
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-read-1",
                        "type": "function",
                        "function": {"name": "read_code", "arguments": '{"path":"Demo.java","start_line":1,"end_line":3}'},
                    }
                ],
                "artifact_path": "",
            },
            artifacts=[],
        )


class FakeKnowledgeService:
    def retrieve_skills_for_bug(self, bug_event: BugEvent, top_k: int = 3):
        return [
            RetrievalResult(
                chunk_id="chunk-1",
                doc_id="skill:demo",
                source="skill",
                doc_type="skill",
                project=bug_event.project,
                title="retrieved skill",
                content="retrieved skill content",
                score=0.91,
                metadata={"top_k": top_k},
            )
        ]

    def retrieve_project_docs_for_bug(self, bug_event: BugEvent, session: dict[str, object] | None = None, top_k: int = 5):
        return [
            RetrievalResult(
                chunk_id="doc-chunk-1",
                doc_id="local_doc:api/order.md",
                source="local_doc",
                doc_type="api_doc",
                project=bug_event.project,
                module="order",
                title="Order API",
                content="retrieved project doc content",
                score=0.88,
                metadata={"top_k": top_k},
            )
        ]


class FailingKnowledgeService:
    def retrieve_skills_for_bug(self, bug_event: BugEvent, top_k: int = 3):
        raise AssertionError("RAG should be disabled")

    def retrieve_project_docs_for_bug(self, bug_event: BugEvent, session: dict[str, object] | None = None, top_k: int = 5):
        raise AssertionError("RAG should be disabled")


def test_repair_agent_builds_prompt_template() -> None:
    config = AppConfig()
    agent = RepairAgent(config, ToolRegistry(), PermissionGuard(), TaskManager(task_store=SimpleNamespace(save=lambda *_: None, get=lambda *_: None)), SessionStore(), SkillStore())
    bug_event = BugEvent(bug_id="BUG-1", source="feishu", project="demo", title="t", exception_type="E", message="m", traceback="full stack", fingerprint="fp")

    prompt = agent.build_prompt_template(bug_event, ["skill-a"], {"state": "x", "frame_contexts": [{"filePath": "Demo.java"}]})
    payload = json.loads(prompt)

    assert "compile and test" in prompt
    assert "project-scoped Java keyword searches" in prompt
    assert "finish_patch" in prompt
    assert "no_fix_needed" not in prompt
    assert "BUG-1" in prompt
    assert "traceback" not in payload["bug_event"]
    assert payload["bug_event"]["traceback_omitted"] is True
    assert payload["frame_contexts"] == [{"filePath": "Demo.java"}]
    assert payload["retrieved_skills"] == ["skill-a"]
    assert payload["retrieved_project_docs"] == []
    assert "skills" not in payload
    assert "frame_contexts" not in payload["session"]
    assert "tools" not in payload


def test_repair_agent_uses_retrieved_skills_not_all_skills() -> None:
    skill_store = SkillStore()
    skill_store.put("demo-all-skill", "this full skill should not be injected")
    agent = RepairAgent(
        AppConfig(),
        ToolRegistry(),
        PermissionGuard(),
        TaskManager(task_store=SimpleNamespace(save=lambda *_: None, get=lambda *_: None)),
        SessionStore(),
        skill_store,
        knowledge_service=FakeKnowledgeService(),
    )
    bug_event = BugEvent(bug_id="BUG-RAG", source="log", project="demo", title="t", exception_type="NullPointerException", message="boom", fingerprint="fp")

    retrieved = agent._retrieve_skills(bug_event)
    prompt = agent.build_prompt_template(bug_event, retrieved, {})
    payload = json.loads(prompt)

    assert payload["retrieved_skills"][0]["content"] == "retrieved skill content"
    assert "this full skill should not be injected" not in prompt


def test_repair_agent_prompt_contains_retrieved_project_docs() -> None:
    agent = RepairAgent(
        AppConfig(),
        ToolRegistry(),
        PermissionGuard(),
        TaskManager(task_store=SimpleNamespace(save=lambda *_: None, get=lambda *_: None)),
        SessionStore(),
        SkillStore(),
        knowledge_service=FakeKnowledgeService(),
    )
    bug_event = BugEvent(bug_id="BUG-DOC", source="log", project="demo", title="t", exception_type="E", message="m", fingerprint="fp")

    project_docs = agent._retrieve_project_docs(bug_event, {})
    prompt = agent.build_prompt_template(bug_event, [], {}, project_docs)
    payload = json.loads(prompt)

    assert "business constraints" in prompt
    assert payload["retrieved_project_docs"][0]["content"] == "retrieved project doc content"
    assert payload["retrieved_project_docs"][0]["doc_type"] == "api_doc"


def test_repair_agent_skips_project_docs_when_rag_disabled() -> None:
    config = AppConfig()
    config.rag.enabled = False
    agent = RepairAgent(
        config,
        ToolRegistry(),
        PermissionGuard(),
        TaskManager(task_store=SimpleNamespace(save=lambda *_: None, get=lambda *_: None)),
        SessionStore(),
        SkillStore(),
        knowledge_service=FailingKnowledgeService(),
    )
    bug_event = BugEvent(bug_id="BUG-DISABLED", source="log", project="demo", title="t", exception_type="E", message="m", fingerprint="fp")

    assert agent._retrieve_project_docs(bug_event, {}) == []


def test_repair_agent_converts_tools_to_openai_function_specs() -> None:
    agent = RepairAgent(AppConfig(), ToolRegistry(), PermissionGuard(), TaskManager(task_store=SimpleNamespace(save=lambda *_: None, get=lambda *_: None)), SessionStore(), SkillStore())

    tools = agent._openai_tools()
    names = {tool["function"]["name"] for tool in tools}
    read_code = next(tool for tool in tools if tool["function"]["name"] == "read_code")
    search_skill = next(tool for tool in tools if tool["function"]["name"] == "search_skill")
    search_project_doc = next(tool for tool in tools if tool["function"]["name"] == "search_project_doc")

    assert "read_code" in names
    assert "search_skill" in names
    assert "search_project_doc" in names
    assert "finish_patch" in names
    assert "no_fix_needed" not in names
    assert "apply_test_patch" not in names
    assert read_code["type"] == "function"
    assert read_code["function"]["parameters"]["additionalProperties"] is False
    assert search_skill["function"]["parameters"]["required"] == ["query", "project"]
    assert search_project_doc["function"]["parameters"]["required"] == ["query", "project"]


def test_repair_agent_uses_native_tool_call_message_history() -> None:
    llm = NativeToolCallLLM()
    agent = RepairAgent(AppConfig(), ToolRegistry(), PermissionGuard(), TaskManager(task_store=SimpleNamespace(save=lambda *_: None, get=lambda *_: None)), SessionStore(), SkillStore(), llm_client=llm)
    bug_event = BugEvent(bug_id="BUG-1", source="log", project="demo", title="t", exception_type="E", message="m", fingerprint="fp")
    messages = [{"role": "system", "content": "{}"}]

    action = agent._ask_llm(messages, [], bug_event, {})
    result = ToolResult(tool="read_code", success=True, exit_code=0, stdout_summary="ok")
    agent._append_tool_result_message(messages, action, result)

    assert llm.last_tool_choice == "auto"
    assert llm.last_tools is not None
    assert action["tool"] == "read_code"
    assert action["arguments"]["path"] == "Demo.java"
    assert messages[-2]["role"] == "assistant"
    assert messages[-2]["tool_calls"][0]["id"] == "call-read-1"
    assert messages[-1]["role"] == "tool"
    assert messages[-1]["tool_call_id"] == "call-read-1"


def test_repair_agent_defaults_search_code_root_to_bug_project_root(tmp_path: Path) -> None:
    config = AppConfig()
    config.project.name = "mall-service"
    config.project.root = str(tmp_path)
    agent = RepairAgent(config, ToolRegistry(), PermissionGuard(), TaskManager(task_store=SimpleNamespace(save=lambda *_: None, get=lambda *_: None)), SessionStore(), SkillStore())
    bug_event = BugEvent(bug_id="BUG-1", source="log", project="mall-service", title="t", exception_type="E", message="m", fingerprint="fp")

    arguments = agent._prepare_tool_arguments("search_code", {"root": ".", "keyword": "ExceptionHandler"}, bug_event)

    assert arguments["root"] == str(tmp_path)
    assert arguments["keyword"] == "ExceptionHandler"


def test_repair_agent_rejects_list_llm_output() -> None:
    agent = RepairAgent(AppConfig(), ToolRegistry(), PermissionGuard(), TaskManager(task_store=SimpleNamespace(save=lambda *_: None, get=lambda *_: None)), SessionStore(), SkillStore())

    action = agent._normalize_llm_action([
        {"tool": "search_code", "arguments": {"keyword": "ExceptionHandler"}, "reason": "find handler"}
    ])

    assert action["tool"] == "__invalid_llm_output__"
    assert "top-level JSON must be an object" in action["reason"]


def test_repair_agent_rejects_json_action_from_markdown_response() -> None:
    agent = RepairAgent(AppConfig(), ToolRegistry(), PermissionGuard(), TaskManager(task_store=SimpleNamespace(save=lambda *_: None, get=lambda *_: None)), SessionStore(), SkillStore())

    payload = (
        "I need to examine the MallUser class.\n\n"
        "```json\n"
        "{\n"
        '  "tool": "read_code",\n'
        '  "arguments": {"path": "MallUser.java"},\n'
        '  "reason": "Examine getProfileJson"\n'
        "}\n"
        "```"
    )

    parsed = agent._parse_llm_action_payload(payload)

    assert parsed is None


def test_repair_agent_accepts_pure_json_action() -> None:
    agent = RepairAgent(AppConfig(), ToolRegistry(), PermissionGuard(), TaskManager(task_store=SimpleNamespace(save=lambda *_: None, get=lambda *_: None)), SessionStore(), SkillStore())

    parsed = agent._parse_llm_action_payload('{"tool":"read_code","arguments":{"path":"MallUser.java"},"reason":"Examine getProfileJson"}')
    action = agent._normalize_llm_action(parsed)

    assert action == {
        "tool": "read_code",
        "arguments": {"path": "MallUser.java"},
        "reason": "Examine getProfileJson",
    }


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
    session = session_store.get("BUG-1")
    assert session["agent_branch"] == "agent-fix/bug-1"
    assert session["base_branch"] == "main"
    assert session["feishu_review_result"]["success"] is True


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


def _test_project_root() -> Path:
    return Path(Path.cwd().anchor) / "czz_aid_test_project" / "mall-service"


def test_repair_agent_accepts_project_advice_patch_with_frame_context() -> None:
    project_root = _test_project_root()
    frame_file = project_root / "src" / "main" / "java" / "com" / "demo" / "service" / "OrderService.java"
    advice_file = project_root / "src" / "main" / "java" / "com" / "demo" / "advice" / "GlobalExceptionHandler.java"
    config = AppConfig()
    config.project.root = str(project_root)
    agent = RepairAgent(config, ToolRegistry(), PermissionGuard(), TaskManager(task_store=SimpleNamespace(save=lambda *_: None, get=lambda *_: None)), SessionStore(), SkillStore())
    result = ToolResult(tool="edit_code", success=True, exit_code=0, data={"path": str(advice_file)}, artifacts=[str(advice_file)])

    assert agent._is_valid_patch(result, {"frame_contexts": [{"filePath": str(frame_file)}]}) is True


def test_repair_agent_accepts_project_test_patch_with_frame_context() -> None:
    project_root = _test_project_root()
    frame_file = project_root / "src" / "main" / "java" / "com" / "demo" / "controller" / "OrderController.java"
    test_file = project_root / "src" / "test" / "java" / "com" / "demo" / "controller" / "OrderControllerTest.java"
    config = AppConfig()
    config.project.root = str(project_root)
    agent = RepairAgent(config, ToolRegistry(), PermissionGuard(), TaskManager(task_store=SimpleNamespace(save=lambda *_: None, get=lambda *_: None)), SessionStore(), SkillStore())
    result = ToolResult(tool="edit_code", success=True, exit_code=0, data={"path": str(test_file)}, artifacts=[str(test_file)])

    assert agent._is_valid_patch(result, {"frame_contexts": [{"filePath": str(frame_file)}]}) is True


def test_repair_agent_rejects_project_java_outside_source_roots_with_frame_context() -> None:
    project_root = _test_project_root()
    frame_file = project_root / "src" / "main" / "java" / "com" / "demo" / "service" / "OrderService.java"
    scratch_file = project_root / "docs" / "Scratch.java"
    config = AppConfig()
    config.project.root = str(project_root)
    agent = RepairAgent(config, ToolRegistry(), PermissionGuard(), TaskManager(task_store=SimpleNamespace(save=lambda *_: None, get=lambda *_: None)), SessionStore(), SkillStore())
    result = ToolResult(tool="edit_code", success=True, exit_code=0, data={"path": str(scratch_file)}, artifacts=[str(scratch_file)])

    assert agent._is_valid_patch(result, {"frame_contexts": [{"filePath": str(frame_file)}]}) is False


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


def test_repair_agent_create_pr_force_pushes_branch(tmp_path: Path) -> None:
    source = tmp_path / "Demo.java"
    source.write_text("class Demo {}\n", encoding="utf-8")
    config = AppConfig()
    config.project.root = str(tmp_path)
    config.gitee.owner = "ch6enle"
    config.gitee.repo = "agent_test_1"
    config.gitee.token = "real-token"
    agent = RepairAgent(config, ToolRegistry(), PermissionGuard(), TaskManager(task_store=SimpleNamespace(save=lambda *_: None, get=lambda *_: None)), SessionStore(), SkillStore())
    commands: list[list[str]] = []

    def fake_run_git(cwd: Path, command: list[str]) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    agent._run_git = fake_run_git
    agent._create_gitee_pull_request = lambda owner, repo, branch, base_branch, bug_event: ToolResult(
        tool="create_pr",
        success=True,
        exit_code=0,
        stdout_summary="https://gitee.test/pr/1",
        stderr_summary="",
        data={"pr_url": "https://gitee.test/pr/1"},
        artifacts=[],
    )
    history = [{"tool": "edit_code", "result": {"success": True, "artifacts": [str(source)], "data": {"path": str(source)}}}]
    bug_event = BugEvent(bug_id="BUG-PR", source="log", project="demo", title="", exception_type="E", message="", fingerprint="fp")

    result = agent._create_pr(SimpleNamespace(id="task-1", bug_id="BUG-PR"), bug_event, history)

    assert result.success is True
    assert commands[-1] == ["git", "push", "--force", "-u", "origin", "agent-fix/bug-pr"]


def test_repair_agent_sends_feishu_help_on_failure() -> None:
    config = AppConfig()
    registry = ToolRegistry()
    feishu = RecordingFeishuTool()
    registry.register(feishu)
    session_store = SessionStore()
    agent = RepairAgent(config, registry, PermissionGuard(), TaskManager(task_store=SimpleNamespace(save=lambda *_: None, get=lambda *_: None)), session_store, SkillStore())
    bug_event = BugEvent(bug_id="BUG-H", source="log", project="demo", title="NPE", exception_type="NullPointerException", message="x", top_business_frame="Demo.java:1", fingerprint="fp")
    last_result = ToolResult(tool="run_test", success=False, exit_code=1, stderr_summary="test failed", data={}, artifacts=[])

    result = agent._send_feishu_help(bug_event, {}, last_result)

    assert result.success is True
    assert feishu.payloads[0]["action"] == "send_help_card"
    assert session_store.get("feishu_help:BUG-H")["feishu_result"]["success"] is True


def test_repair_agent_notifies_whitelist_denied_commands() -> None:
    config = AppConfig()
    registry = ToolRegistry()
    feishu = RecordingFeishuTool()
    registry.register(feishu)
    session_store = SessionStore()
    agent = RepairAgent(config, registry, PermissionGuard(), TaskManager(task_store=SimpleNamespace(save=lambda *_: None, get=lambda *_: None)), session_store, SkillStore())
    bug_event = BugEvent(bug_id="BUG-D", source="log", project="demo", title="NPE", exception_type="NullPointerException", message="x", top_business_frame="Demo.java:1", fingerprint="fp")

    result = agent._notify_whitelist_denial(
        bug_event,
        {},
        {"tool": "run_command", "reason": "command not in whitelist", "arguments": {"command": "curl https://example.com"}},
    )

    assert result.success is True
    assert feishu.payloads[0]["action"] == "send_help_card"
    assert "whitelist denied" in feishu.payloads[0]["args"]["last_result"]["stderr_summary"]
    assert session_store.get("whitelist_denied:BUG-D")["denied_entry"]["tool"] == "run_command"


def test_repair_agent_sends_feishu_review_request() -> None:
    config = AppConfig()
    registry = ToolRegistry()
    feishu = RecordingFeishuTool()
    registry.register(feishu)
    session_store = SessionStore()
    agent = RepairAgent(config, registry, PermissionGuard(), TaskManager(task_store=SimpleNamespace(save=lambda *_: None, get=lambda *_: None)), session_store, SkillStore())
    task = RepairTask(bug_id="BUG-R", project="demo", agent_branch="agent-fix/bug-r", base_branch="main")
    bug_event = BugEvent(bug_id="BUG-R", source="log", project="demo", title="NPE", exception_type="NullPointerException", message="x", top_business_frame="Demo.java:1", fingerprint="fp")
    pr_result = ToolResult(tool="create_pr", success=True, exit_code=0, stdout_summary="https://gitee.test/pr/1", data={"pr_url": "https://gitee.test/pr/1", "branch": "agent-fix/bug-r", "base_branch": "main"}, artifacts=[])
    compile_result = ToolResult(tool="run_compile", success=True, exit_code=0, stdout_summary="compile ok", data={}, artifacts=[])
    test_result = ToolResult(tool="run_test", success=True, exit_code=0, stdout_summary="test ok", data={}, artifacts=[])

    result = agent._send_feishu_review_request(task, bug_event, {}, pr_result, compile_result, test_result)

    assert result.success is True
    assert feishu.payloads[0]["action"] == "send_review_request_card"
    assert session_store.get("BUG-R")["feishu_review_result"]["success"] is True
