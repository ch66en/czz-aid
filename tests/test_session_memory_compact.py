from __future__ import annotations

import json
from pathlib import Path

from agent.config import AppConfig
from agent.core.permission_guard import PermissionGuard
from agent.core.repair_agent import RepairAgent
from agent.core.task_manager import TaskManager
from agent.core.tool_registry import ToolRegistry
from agent.models import BugEvent, ToolResult
from agent.session_memory import ContextCompactor, MemoryUpdateEvent, SessionMemoryAgent, SessionMemoryStore
from agent.storage.session_store import SessionStore
from agent.storage.skill_store import SkillStore


class NoopTaskStore:
    def save(self, *_args) -> None:
        return None

    def get(self, *_args):
        return None


def _config(tmp_path: Path) -> AppConfig:
    config = AppConfig()
    config.session.root_dir = str(tmp_path / "sessions")
    config.session_memory.async_enabled = False
    config.session_memory.max_summary_chars = 12000
    return config


def test_session_memory_agent_writes_summary_after_tool_result(tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = SessionMemoryStore(config)
    agent = SessionMemoryAgent(config, store)
    event = MemoryUpdateEvent(
        bug_id="BUG-1",
        tool="read_code",
        reason="inspect failing method",
        result={"tool": "read_code", "success": True, "exit_code": 0, "stdout_summary": "loaded OrderService", "stderr_summary": ""},
    )

    agent.enqueue(event)

    summary = store.read_summary("BUG-1")
    assert "# Current State" in summary
    assert "`read_code` success" in summary
    assert "loaded OrderService" in summary


def test_context_compactor_preserves_recent_tool_pair(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.context_compact.context_window_tokens = 120
    config.context_compact.reserved_output_tokens = 0
    config.context_compact.auto_ratio = 0.1
    config.context_compact.keep_recent_tool_pairs = 1
    store = SessionMemoryStore(config)
    store.write_summary("BUG-PAIR", "# Current State\n- remembered state\n")
    compactor = ContextCompactor(config, store)
    messages = [
        {"role": "system", "content": json.dumps({"role": "repair-agent"})},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "old-call", "type": "function", "function": {"name": "search_code", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "old-call", "content": "old result" * 200},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "new-call", "type": "function", "function": {"name": "read_code", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "new-call", "content": "new result"},
    ]

    result = compactor.compact_if_needed("BUG-PAIR", messages, history=[{"tool": "read_code"}])

    assert result.compacted is True
    assert result.messages[0]["role"] == "system"
    assert "session_memory" in result.messages[0]["content"]
    assert result.messages[-2]["tool_calls"][0]["id"] == "new-call"
    assert result.messages[-1]["tool_call_id"] == "new-call"
    assert "old-call" not in json.dumps(result.messages, ensure_ascii=False)


def test_repair_agent_enqueues_session_memory_update(tmp_path: Path) -> None:
    config = _config(tmp_path)
    session_store = SessionStore()
    repair_agent = RepairAgent(
        config,
        ToolRegistry(),
        PermissionGuard(),
        TaskManager(task_store=NoopTaskStore()),
        session_store,
        SkillStore(),
    )
    bug_event = BugEvent(bug_id="BUG-MEM", source="log", project="demo", title="t", exception_type="E", message="m", fingerprint="fp")
    session: dict[str, object] = {"session_memory_path": repair_agent.session_memory_agent.ensure("BUG-MEM")}
    action = {"tool": "search_code", "arguments": {"keyword": "OrderService"}, "reason": "find code", "tool_call_id": "call-1"}
    result = ToolResult(tool="search_code", success=True, exit_code=0, stdout_summary="found 1 result", stderr_summary="", data={}, artifacts=[])

    repair_agent._append_session_tool_call(session, action, result)
    repair_agent._save_session("BUG-MEM", session)
    repair_agent._enqueue_memory_update(bug_event, session, action, result, history=[action], messages=[])
    repair_agent.session_memory_agent.wait_for_idle()

    summary = repair_agent.session_memory_store.read_summary("BUG-MEM")
    assert "`search_code` success" in summary
    assert "find code" in summary
    assert session_store.get("BUG-MEM")["session_revision"] == 1
