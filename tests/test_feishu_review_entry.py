"""验证飞书审核事件入口。"""

import json

from agent.config import AppConfig
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


def test_feishu_review_entry_handles_review_passed(tmp_path) -> None:
    session_store = SessionStore()
    skill_store = SkillStore()
    bug_id = "bug-1"
    session_store.put(bug_id, {"base_branch": "main", "agent_branch": "agent-fix/demo", "artifact_paths": {}})
    session_store.put(f"bug_event:{bug_id}", {"bug_id": bug_id, "source": "feishu", "project": "demo", "title": "NPE", "exception_type": "NullPointerException", "message": "x", "traceback": "", "fingerprint": "fp"})
    agent = ReflectionSubAgent(AppConfig(workspace=str(tmp_path)), session_store=session_store, skill_store=skill_store, llm_client=FakeLLM())

    result = agent.handle_review_event({"event_type": "review_passed", "bug_id": bug_id, "reviewer": "dev", "comment": "ok"})

    assert result.success is True
    assert result.skill_artifact is not None


def test_feishu_review_entry_handles_review_failed_with_branch(tmp_path) -> None:
    session_store = SessionStore()
    skill_store = SkillStore()
    bug_id = "bug-2"
    session_store.put(bug_id, {"base_branch": "main", "agent_branch": "agent-fix/demo", "artifact_paths": {}})
    session_store.put(f"bug_event:{bug_id}", {"bug_id": bug_id, "source": "feishu", "project": "demo", "title": "NPE", "exception_type": "NullPointerException", "message": "x", "traceback": "", "fingerprint": "fp"})
    agent = ReflectionSubAgent(AppConfig(workspace=str(tmp_path)), session_store=session_store, skill_store=skill_store, llm_client=FakeLLM())

    result = agent.handle_review_event(json.loads('{"event_type": "review_failed", "bug_id": "bug-2", "reviewer": "dev", "human_fix_branch": "human-fix/1", "comment": "bad"}'))

    assert result.success is True
