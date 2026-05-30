from __future__ import annotations

"""Prompt compaction utilities that preserve OpenAI tool-call pairs."""

import json
from datetime import datetime
from typing import Any

from agent.config import AppConfig
from agent.session_memory.models import CompactionResult, CompactionState
from agent.session_memory.store import SessionMemoryStore


class ContextCompactor:
    """Compact LLM messages when the configured token budget is exceeded."""

    def __init__(self, config: AppConfig, store: SessionMemoryStore | None = None) -> None:
        self.config = config
        self.store = store or SessionMemoryStore(config)
        self.states: dict[str, CompactionState] = {}

    def compact_if_needed(self, bug_id: str, messages: list[dict[str, Any]], history: list[dict[str, Any]] | None = None) -> CompactionResult:
        if not self.config.context_compact.enabled:
            return self._result(False, "disabled", messages)
        tokens_before = self.estimate_tokens(messages)
        usable_tokens = max(1, self.config.context_compact.context_window_tokens - self.config.context_compact.reserved_output_tokens)
        ratio_before = tokens_before / usable_tokens
        if ratio_before < self.config.context_compact.auto_ratio:
            return self._result(False, "below_threshold", messages, tokens_before=tokens_before, ratio_before=ratio_before)

        summary = self.store.read_summary(bug_id)
        recent = self._recent_messages_preserving_tool_pairs(messages, self.config.context_compact.keep_recent_tool_pairs)
        compacted = [self._compact_system_message(messages, summary)] + recent
        tokens_after = self.estimate_tokens(compacted)
        ratio_after = tokens_after / usable_tokens
        state = self.states.get(bug_id) or CompactionState(bug_id=bug_id)
        state.compacted = True
        state.compact_count += 1
        state.tokens_before = tokens_before
        state.tokens_after = tokens_after
        state.usage_ratio_before = ratio_before
        state.usage_ratio_after = ratio_after
        state.last_reason = "auto_threshold"
        state.last_compacted_at = datetime.utcnow()
        state.last_compacted_message_index = len(messages)
        state.last_compacted_history_index = len(history or [])
        self.states[bug_id] = state
        return CompactionResult(
            compacted=True,
            reason="auto_threshold",
            messages=compacted,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            usage_ratio_before=ratio_before,
            usage_ratio_after=ratio_after,
        )

    def estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        text = json.dumps(messages, ensure_ascii=False, default=str)
        return max(1, len(text) // 4)

    def _compact_system_message(self, messages: list[dict[str, Any]], summary: str) -> dict[str, Any]:
        original = messages[0] if messages else {"role": "system", "content": ""}
        content = str(original.get("content") or "")
        try:
            payload = json.loads(content)
            if isinstance(payload, dict):
                payload["session_memory"] = summary
                payload["compacted"] = True
                return {"role": "system", "content": json.dumps(payload, ensure_ascii=False, default=str)}
        except json.JSONDecodeError:
            pass
        return {"role": "system", "content": f"{content}\n\nSession memory:\n{summary}\n\nContext was compacted."}

    def _recent_messages_preserving_tool_pairs(self, messages: list[dict[str, Any]], keep_pairs: int) -> list[dict[str, Any]]:
        if keep_pairs <= 0:
            return []
        selected: list[dict[str, Any]] = []
        pairs = 0
        index = len(messages) - 1
        while index > 0 and pairs < keep_pairs:
            message = messages[index]
            role = message.get("role")
            if role == "tool":
                pair_start = self._find_assistant_for_tool(messages, index, str(message.get("tool_call_id") or ""))
                selected[0:0] = messages[pair_start : index + 1]
                pairs += 1
                index = pair_start - 1
                continue
            if role == "assistant" and message.get("tool_calls"):
                selected.insert(0, message)
                pairs += 1
            elif role != "system":
                selected.insert(0, message)
            index -= 1
        return selected

    def _find_assistant_for_tool(self, messages: list[dict[str, Any]], tool_index: int, call_id: str) -> int:
        for index in range(tool_index - 1, 0, -1):
            message = messages[index]
            if message.get("role") != "assistant":
                continue
            calls = message.get("tool_calls") or []
            if any(isinstance(call, dict) and str(call.get("id") or "") == call_id for call in calls):
                return index
        return max(1, tool_index - 1)

    def _result(
        self,
        compacted: bool,
        reason: str,
        messages: list[dict[str, Any]],
        *,
        tokens_before: int | None = None,
        ratio_before: float = 0.0,
    ) -> CompactionResult:
        tokens = tokens_before if tokens_before is not None else self.estimate_tokens(messages)
        return CompactionResult(
            compacted=compacted,
            reason=reason,
            messages=messages,
            tokens_before=tokens,
            tokens_after=tokens,
            usage_ratio_before=ratio_before,
            usage_ratio_after=ratio_before,
        )
