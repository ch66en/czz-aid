"""验证反思子代理的硬编码流程。"""

from agent.config import AppConfig
from agent.models import ToolResult
from agent.reflection.reflection_subagent import ReflectionSubAgent
from agent.storage.session_store import SessionStore
from agent.storage.skill_store import SkillStore


class FakeLLM:
    def chat(self, messages):
        class Response:
            pass

        response = Response()
        response.data = {"content": "适用场景：测试。典型信号：NPE。推荐步骤：先编译后测试。避免事项：跳过校验。"}
        return response


class FakeGitTool:
    def __init__(self) -> None:
        self.payloads = []

    def run(self, payload):
        self.payloads.append(payload)
        target = payload["args"]["target"]
        if target == "agent-fix/demo":
            diff = "diff --git a/src/Agent.java b/src/Agent.java\n+++ b/src/Agent.java\n+agent fix\n"
        else:
            diff = "diff --git a/src/Human.java b/src/Human.java\n+++ b/src/Human.java\n+human fix\n"
        return ToolResult(tool="git_tool", success=True, exit_code=0, stdout_summary=diff, stderr_summary="", data={}, artifacts=[])


def _seed_session(session_store: SessionStore, bug_id: str) -> None:
    session_store.put(bug_id, {"base_branch": "main", "agent_branch": "agent-fix/demo", "artifact_paths": {}})
    session_store.put(f"bug_event:{bug_id}", {"bug_id": bug_id, "source": "feishu", "project": "demo", "title": "NPE", "exception_type": "NullPointerException", "message": "x", "traceback": "", "fingerprint": "fp"})


def test_reflection_review_passed_creates_skill(tmp_path) -> None:
    session_store = SessionStore()
    skill_store = SkillStore()
    bug_id = "bug-1"
    _seed_session(session_store, bug_id)
    agent = ReflectionSubAgent(config=AppConfig(workspace=str(tmp_path)), session_store=session_store, skill_store=skill_store, llm_client=FakeLLM())

    result = agent.handle_review_event({"event_type": "review_passed", "bug_id": bug_id, "reviewer": "dev", "comment": "ok"})

    assert result.success is True
    assert result.skill_artifact is not None
    assert skill_store.get(result.skill_artifact.meta.name) is not None
    assert "## 适用场景" in result.skill_artifact.markdown
    assert "## 验证方式" in result.skill_artifact.markdown


def test_reflection_review_failed_requires_human_branch(tmp_path) -> None:
    session_store = SessionStore()
    skill_store = SkillStore()
    bug_id = "bug-2"
    _seed_session(session_store, bug_id)
    agent = ReflectionSubAgent(config=AppConfig(workspace=str(tmp_path)), session_store=session_store, skill_store=skill_store, llm_client=FakeLLM())

    result = agent.handle_review_event({"event_type": "review_failed", "bug_id": bug_id, "reviewer": "dev", "comment": "bad"})

    assert result.success is False
    assert "human_fix_branch" in result.message


def test_reflection_reads_native_tool_call_history() -> None:
    session_store = SessionStore()
    skill_store = SkillStore()
    agent = ReflectionSubAgent(config=AppConfig(), session_store=session_store, skill_store=skill_store, llm_client=FakeLLM())

    history = agent._tool_history_from_session(
        {
            "tool_calls": [
                {
                    "tool_call_id": "call-1",
                    "name": "read_code",
                    "arguments": {"path": "Demo.java"},
                    "result": {"success": True},
                }
            ]
        }
    )

    assert '"type": "tool_call"' in history
    assert '"tool_call_id": "call-1"' in history
    assert '"name": "read_code"' in history


def test_reflection_skill_name_is_windows_safe(tmp_path) -> None:
    session_store = SessionStore()
    skill_store = SkillStore()
    bug_id = "log-0f98749269ac"
    session_store.put(bug_id, {"base_branch": "main", "agent_branch": "agent-fix/demo", "artifact_paths": {}})
    session_store.put(
        f"bug_event:{bug_id}",
        {
            "bug_id": bug_id,
            "source": "feishu",
            "project": "agent_test_1",
            "title": "Auto detected: java.lang.ArrayIndexOutOfBoundsException",
            "exception_type": "java.lang.ArrayIndexOutOfBoundsException",
            "message": "x",
            "traceback": "",
            "fingerprint": "fp",
        },
    )
    agent = ReflectionSubAgent(config=AppConfig(workspace=str(tmp_path)), session_store=session_store, skill_store=skill_store, llm_client=FakeLLM())

    result = agent.handle_review_event({"event_type": "review_passed", "bug_id": bug_id, "reviewer": "dev", "comment": "ok"})

    assert result.success is True
    assert result.skill_artifact is not None
    assert ":" not in result.skill_artifact.skill_dir.name
    assert result.skill_artifact.skill_dir.exists()


def test_reflection_review_failed_compares_agent_and_human_diff(tmp_path) -> None:
    session_store = SessionStore()
    skill_store = SkillStore()
    bug_id = "bug-3"
    _seed_session(session_store, bug_id)
    git_tool = FakeGitTool()
    agent = ReflectionSubAgent(config=AppConfig(workspace=str(tmp_path)), session_store=session_store, skill_store=skill_store, llm_client=FakeLLM(), git_tool=git_tool)

    result = agent.handle_review_event({"event_type": "review_failed", "bug_id": bug_id, "reviewer": "dev", "human_fix_branch": "human-fix/1", "comment": "bad"})

    assert result.success is True
    assert git_tool.payloads[0]["args"] == {"base": "main", "target": "agent-fix/demo"}
    assert git_tool.payloads[1]["args"] == {"base": "main", "target": "human-fix/1"}
    session = session_store.get(bug_id)
    assert session["diff_analysis"]["agent_only_files"] == ["src/Agent.java"]
    assert session["diff_analysis"]["human_only_files"] == ["src/Human.java"]
    assert "## 人工修复关键点" in result.skill_artifact.markdown
