"""验证 Legacy Full Compact 的触发、恢复和防护逻辑。"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from agent.config import AppConfig
from agent.core.compact_transcript import CompactTranscript
from agent.core.legacy_full_compactor import LegacyFullCompactor, TRUNCATED_HISTORY_MARKER
from agent.models import ToolResult


class ControlledEstimator:
    """为测试提供稳定阈值，避免测试依赖 JSON 长度细节。"""

    def __init__(self, before: int, after: int) -> None:
        self.before = before
        self.after = after

    def estimate_context(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> int:
        del tools
        has_boundary = any("legacy_full_compact_boundary" in str(message.get("content", "")) for message in messages)
        return self.after if has_boundary else self.before


class FakeSummaryLLM:
    """依次返回预设摘要响应，并记录 compact 调用参数。"""

    def __init__(self, responses: list[ToolResult]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages, tools=None, max_tokens=None, **kwargs):
        self.calls.append({"messages": messages, "tools": tools, "max_tokens": max_tokens, **kwargs})
        return self.responses.pop(0)


def _config(tmp_path: Path, **compact_overrides: Any) -> AppConfig:
    compact = {
        "context_window_tokens": 200,
        "summary_max_output_tokens": 20,
        "normal_output_reserve_tokens": 20,
        "buffer_tokens": 30,
        "keep_recent_rounds": 1,
    }
    compact.update(compact_overrides)
    return AppConfig(
        project={"root": str(tmp_path)},
        session={"root_dir": str(tmp_path / "sessions")},
        compact=compact,
    )


def _round(index: int) -> list[dict[str, Any]]:
    call_id = f"call-{index}"
    return [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": "read_code", "arguments": f'{{"path":"Demo{index}.java"}}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": call_id, "content": f"result-{index}"},
    ]


def _messages(round_count: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = [{"role": "system", "content": "repair rules"}]
    for index in range(1, round_count + 1):
        result.extend(_round(index))
    return result


def _summary_success(content: str = "# 当前状态\n继续修复") -> ToolResult:
    return ToolResult(
        tool="llm_chat",
        success=True,
        exit_code=0,
        data={
            "content": content,
            "token_usage": {"prompt_tokens": 100, "completion_tokens": 20},
        },
    )


def test_below_threshold_does_not_compact(tmp_path: Path) -> None:
    llm = FakeSummaryLLM([_summary_success()])
    compactor = LegacyFullCompactor(_config(tmp_path), llm, estimator=ControlledEstimator(before=149, after=50))  # type: ignore[arg-type]

    result = compactor.compact_if_needed(
        bug_id="BUG-1",
        messages=_messages(3),
        session={},
        rebuilt_system_prompt="repair rules",
        tools=[],
    )

    assert result.compacted is False
    assert result.reason == "below_threshold"
    assert llm.calls == []


def test_compact_summarizes_old_rounds_and_keeps_recent_round_intact(tmp_path: Path) -> None:
    llm = FakeSummaryLLM([_summary_success()])
    compactor = LegacyFullCompactor(_config(tmp_path), llm, estimator=ControlledEstimator(before=170, after=60))  # type: ignore[arg-type]
    messages = _messages(3)
    session = {"tool_calls": [{"name": "audit-only", "result": {"success": True}}]}
    original_tool_calls = deepcopy(session["tool_calls"])

    result = compactor.compact_if_needed(
        bug_id="BUG-2",
        messages=messages,
        session=session,
        rebuilt_system_prompt="repair rules",
        tools=[{"type": "function", "function": {"name": "read_code"}}],
    )

    assert result.compacted is True
    assert result.dropped_round_count == 2
    assert result.messages[-2:] == _round(3)
    assert session["tool_calls"] == original_tool_calls
    assert llm.calls[0]["tools"] is None
    assert llm.calls[0]["max_tokens"] == 20
    assert result.summary_input_tokens == 100
    assert result.summary_output_tokens == 20
    assert Path(result.summary_path).exists()
    assert Path(result.transcript_path).exists()
    boundary = json.loads(result.messages[1]["content"])
    assert boundary["transcript_path"] == result.transcript_path


def test_legacy_compact_preserves_rag_context_in_rebuilt_system_prompt(tmp_path: Path) -> None:
    llm = FakeSummaryLLM([_summary_success()])
    compactor = LegacyFullCompactor(_config(tmp_path), llm, estimator=ControlledEstimator(before=170, after=60))  # type: ignore[arg-type]
    prompt = json.dumps(
        {
            "role": "repair",
            "rag_context": {
                "hard_constraints": [{"text": "approved rule"}],
                "soft_hints": [],
                "confidence": "high",
            },
        },
        ensure_ascii=False,
    )
    messages = [{"role": "system", "content": prompt}, *_round(1), *_round(2), *_round(3)]

    result = compactor.compact_if_needed(
        bug_id="BUG-RAG-COMPACT",
        messages=messages,
        session={"rag_status": {"status": "success"}},
        rebuilt_system_prompt=prompt,
        tools=[],
    )

    assert result.compacted is True
    assert result.messages[0]["content"] == prompt
    assert "approved rule" in result.messages[0]["content"]


def test_select_recent_rounds_drops_orphan_tool_message(tmp_path: Path) -> None:
    compactor = LegacyFullCompactor(_config(tmp_path, keep_recent_rounds=2), FakeSummaryLLM([]))
    messages = [
        {"role": "system", "content": "rules"},
        {"role": "tool", "tool_call_id": "orphan", "content": "bad"},
        *_round(1),
    ]

    assert compactor.select_recent_rounds(messages) == _round(1)


def test_restore_recent_files_reads_latest_disk_content_and_respects_budget(tmp_path: Path) -> None:
    source = tmp_path / "Demo.java"
    source.write_text("class Demo { String value = \"latest\"; }\n", encoding="utf-8")
    config = _config(tmp_path, restore_max_files=1, restore_max_chars_per_file=24, restore_total_chars=24)
    compactor = LegacyFullCompactor(config, FakeSummaryLLM([]))
    session = {
        "tool_calls": [
            {
                "name": "read_code",
                "arguments": {"path": str(source)},
                "result": {"success": True, "data": {"path": str(source), "content": "stale"}},
            }
        ]
    }

    restored = compactor.restore_recent_files(session)

    assert len(restored) == 1
    assert restored[0]["path"] == str(source.resolve())
    assert restored[0]["content"] != "stale"
    assert len(restored[0]["content"]) <= 24
    assert restored[0]["truncated"] is True


def test_ptl_retry_drops_complete_old_round_before_retry(tmp_path: Path) -> None:
    failure = ToolResult(tool="llm_chat", success=False, exit_code=1, stderr_summary="maximum context length exceeded")
    llm = FakeSummaryLLM([failure, _summary_success()])
    compactor = LegacyFullCompactor(_config(tmp_path), llm, estimator=ControlledEstimator(before=170, after=60))  # type: ignore[arg-type]

    result = compactor.compact_if_needed(
        bug_id="BUG-PTL",
        messages=_messages(6),
        session={},
        rebuilt_system_prompt="repair rules",
        tools=[],
    )

    assert result.compacted is True
    assert result.ptl_retry_count == 1
    retried_messages = llm.calls[1]["messages"]
    marker = retried_messages[1]["content"]
    assert marker.startswith(TRUNCATED_HISTORY_MARKER)
    assert f"read the full transcript at: {result.transcript_path}" in marker
    assert not any(message.get("tool_call_id") == "call-1" for message in retried_messages)
    transcript = Path(result.transcript_path).read_text(encoding="utf-8")
    assert "call-1" in transcript


def test_circuit_breaker_blocks_main_call_above_hard_threshold(tmp_path: Path) -> None:
    llm = FakeSummaryLLM([])
    compactor = LegacyFullCompactor(_config(tmp_path), llm, estimator=ControlledEstimator(before=190, after=60))  # type: ignore[arg-type]
    session = {"legacy_compaction_state": {"consecutive_failures": 3}}

    result = compactor.compact_if_needed(
        bug_id="BUG-BLOCK",
        messages=_messages(3),
        session=session,
        rebuilt_system_prompt="repair rules",
        tools=[],
    )

    assert result.compacted is False
    assert result.blocked is True
    assert result.reason == "circuit_breaker_open"
    assert llm.calls == []


def test_truncate_head_for_ptl_retry_keeps_tool_pairs(tmp_path: Path) -> None:
    compactor = LegacyFullCompactor(_config(tmp_path), FakeSummaryLLM([]))

    transcript_path = str(tmp_path / "sessions" / "BUG-1" / "transcript.jsonl")
    truncated = compactor.truncate_head_for_ptl_retry(_messages(5), transcript_path)

    assert truncated is not None
    assert truncated[0]["role"] == "system"
    assert truncated[1]["content"].startswith(TRUNCATED_HISTORY_MARKER)
    assert f"read the full transcript at: {transcript_path}" in truncated[1]["content"]
    assert not any(message.get("tool_call_id") == "call-1" for message in truncated)
    assert any(message.get("tool_call_id") == "call-2" for message in truncated)


def test_compact_transcript_is_append_only_and_sanitizes_string_values(tmp_path: Path) -> None:
    transcript = CompactTranscript(_config(tmp_path))

    path = transcript.append_message(
        "BUG-SECRET",
        {"role": "tool", "content": "Authorization: Bearer abc123"},
        source="tool",
    )
    transcript.append_message("BUG-SECRET", {"role": "assistant", "content": "continue"}, source="assistant")

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 2
    assert records[0]["data"]["message"]["content"] == "Authorization: Bearer [REDACTED]"
    assert records[1]["data"]["message"]["content"] == "continue"
