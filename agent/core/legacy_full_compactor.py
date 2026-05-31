from __future__ import annotations

"""实现面向主修复循环的 Legacy Full Compact。"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.config import AppConfig
from agent.core.compact_transcript import CompactTranscript
from agent.core.token_estimator import TokenEstimator
from agent.ingestion.sanitizer import Sanitizer
from agent.llm.openai_compatible_client import OpenAICompatibleClient
from agent.models import LegacyCompactionResult, LegacyCompactionState, ToolResult


SUMMARY_PROMPT = """你正在压缩一个 Java 自动修复 Agent 的历史上下文。

目标：生成一份可以让主修复 Agent 直接继续工作的结构化摘要。

要求：
1. 不要调用工具。
2. 不要输出思考过程。
3. 只输出 Markdown 摘要。
4. 保留准确的文件路径、类名、方法名和行号。
5. 保留已经执行过的工具调用及其关键结果。
6. 保留失败补丁、编译错误、测试错误和回滚原因。
7. 保留权限拒绝、业务约束和人工反馈。
8. 明确当前修复状态和下一步操作。
9. 不要泄露密钥、Token、Webhook 或其他敏感信息。

输出结构：

# 缺陷与修复目标
# 已确认的代码事实
# 已读取的文件与符号
# 已尝试的修改
# 编译、测试与回滚
# 权限拒绝与约束
# 当前状态
# 下一步
"""

TRUNCATED_HISTORY_MARKER = "[earlier conversation truncated for compaction retry]"
RESTORED_FILE_TRUNCATION_MARKER = "\n\n[文件内容已按 compact 恢复预算截断]"


class LegacyFullCompactor:
    """在上下文接近上限时，将旧历史压缩为摘要并保留最近完整轮次。"""

    def __init__(
        self,
        config: AppConfig,
        llm_client: OpenAICompatibleClient | None,
        estimator: TokenEstimator | None = None,
        sanitizer: Sanitizer | None = None,
        transcript: CompactTranscript | None = None,
    ) -> None:
        self.config = config
        self.llm_client = llm_client
        self.estimator = estimator or TokenEstimator()
        self.sanitizer = sanitizer or Sanitizer()
        self.transcript = transcript or CompactTranscript(config, self.sanitizer)

    @property
    def auto_compact_threshold(self) -> int:
        """返回提前触发 compact 的阈值。"""
        compact = self.config.compact
        effective_window = compact.context_window_tokens - compact.summary_max_output_tokens
        return max(0, effective_window - compact.buffer_tokens)

    @property
    def blocking_threshold(self) -> int:
        """返回禁止继续调用主模型的硬阈值。"""
        compact = self.config.compact
        return max(0, compact.context_window_tokens - compact.normal_output_reserve_tokens)

    def compact_if_needed(
        self,
        *,
        bug_id: str,
        messages: list[dict[str, Any]],
        session: dict[str, Any],
        rebuilt_system_prompt: str,
        tools: list[dict[str, Any]],
    ) -> LegacyCompactionResult:
        """必要时生成摘要并返回新的活动消息列表。

        注意：本方法不会接收或修改 ``history``，也不会修改
        ``session["tool_calls"]``。两者是完整审计数据，也是回滚和创建
        PR 的依据。compact 只负责
        收缩下一次主模型请求使用的 ``llm_messages``。
        """
        tokens_before = self.estimator.estimate_context(messages, tools)
        state = self._load_state(session)

        if not self.config.compact.enabled:
            return LegacyCompactionResult(reason="disabled", tokens_before=tokens_before, tokens_after=tokens_before)
        if self.llm_client is None:
            # 离线规则模式不会请求主模型，无需为了 compact 阻断修复流程。
            return LegacyCompactionResult(reason="llm_unavailable", tokens_before=tokens_before, tokens_after=tokens_before)
        if tokens_before < self.auto_compact_threshold:
            return LegacyCompactionResult(reason="below_threshold", tokens_before=tokens_before, tokens_after=tokens_before)
        if state.consecutive_failures >= self.config.compact.max_consecutive_failures:
            return self._record_failure(
                session=session,
                state=state,
                reason="circuit_breaker_open",
                tokens_before=tokens_before,
                increment_failure=False,
            )

        system_messages, round_groups = self._split_system_and_rounds(messages)
        recent_groups = self._select_recent_groups(round_groups)
        # 摘要覆盖完整历史，最近轮次仍会在摘要后原样保留。这样即使重建后
        # 仍然过大、不得不减少最旧的保留轮次，被减少内容也已经进入摘要，
        # 不会因为二次收缩而完全丢失。
        messages_to_summarize = [*system_messages, *self._flatten(round_groups)]
        try:
            # PTL retry may remove early rounds from the summary request. Persist
            # the full active conversation first, so the marker always points to
            # a readable source of exact snippets, errors, and generated text.
            transcript_path = self.transcript.ensure_exists(bug_id, messages_to_summarize)
            session["transcript_path"] = str(transcript_path)
            summary, ptl_retry_count, summary_usage = self._summarize_conversation(
                messages_to_summarize,
                transcript_path=str(transcript_path),
            )
        except (OSError, RuntimeError) as exc:
            return self._record_failure(
                session=session,
                state=state,
                reason=str(exc),
                tokens_before=tokens_before,
            )

        restored_files = self.restore_recent_files(session)
        slim_session_state = self._build_slim_session_state(session)

        # 优先保留配置指定的最近轮次。如果上下文仍然过大，再逐步减少最旧
        # 的保留轮次，最后减少恢复文件。至少保留最后一个完整轮次。
        fitted_messages, fitted_recent_groups, fitted_files = self._fit_compacted_context(
            rebuilt_system_prompt=rebuilt_system_prompt,
            summary=summary,
            slim_session_state=slim_session_state,
            recent_groups=recent_groups,
            restored_files=restored_files,
            transcript_path=str(transcript_path),
            tools=tools,
        )
        tokens_after = self.estimator.estimate_context(fitted_messages, tools)
        if tokens_after >= self.blocking_threshold:
            return self._record_failure(
                session=session,
                state=state,
                reason="blocking_threshold_after_compact",
                tokens_before=tokens_before,
                tokens_after=tokens_after,
            )

        next_compact_count = state.compact_count + 1
        summary_path = self._persist_summary(bug_id, next_compact_count, summary)
        state.compact_count = next_compact_count
        state.consecutive_failures = 0
        state.tokens_before = tokens_before
        state.tokens_after = tokens_after
        state.last_summary_path = str(summary_path)
        state.last_transcript_path = str(transcript_path)
        state.last_error = ""
        state.last_compacted_at = datetime.now(timezone.utc)

        dropped_messages = len(self._flatten(round_groups)) - len(self._flatten(fitted_recent_groups))
        result = LegacyCompactionResult(
            compacted=True,
            reason="token_threshold",
            messages=fitted_messages,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            dropped_round_count=len(round_groups) - len(fitted_recent_groups),
            dropped_message_count=dropped_messages,
            restored_file_count=len(fitted_files),
            ptl_retry_count=ptl_retry_count,
            summary_input_tokens=self._usage_value(summary_usage, "prompt_tokens", "input_tokens"),
            summary_output_tokens=self._usage_value(summary_usage, "completion_tokens", "output_tokens"),
            summary_path=str(summary_path),
            transcript_path=str(transcript_path),
        )
        try:
            self.transcript.append_event(
                bug_id,
                "legacy_full_compact",
                {
                    "summary_path": str(summary_path),
                    "tokens_before": tokens_before,
                    "tokens_after": tokens_after,
                    "ptl_retry_count": ptl_retry_count,
                },
            )
        except OSError:
            # The main compact result is still valid. A later retry will report
            # transcript persistence failure if the stable file becomes unreadable.
            pass
        self._store_state(session, state)
        self._store_result(session, result)
        return result

    def group_messages_by_api_round(self, messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        """按 assistant 响应边界分组，保证工具调用和结果不会被拆开。

        主修复循环要求每轮只调用一个工具。一次典型分组是：
        ``assistant(tool_calls=[call_x]) -> tool(tool_call_id=call_x)``。
        使用 assistant 边界而不是固定消息数量，可以兼容普通文本反馈。
        """
        groups: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        for message in messages:
            if message.get("role") == "assistant" and current:
                groups.append(current)
                current = [message]
            else:
                current.append(message)
        if current:
            groups.append(current)
        return groups

    def select_recent_rounds(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """返回最近完整轮次，不包含开头的 system prompt。"""
        _, groups = self._split_system_and_rounds(messages)
        return self._flatten(self._select_recent_groups(groups))

    def truncate_head_for_ptl_retry(
        self,
        messages: list[dict[str, Any]],
        transcript_path: str = "",
    ) -> list[dict[str, Any]] | None:
        """摘要请求过长时，丢弃最旧约 20% 的完整轮次。

        不能直接 ``messages[n:]``：这样可能保留一个孤立 ``tool`` 消息，
        OpenAI-compatible API 会拒绝请求。
        """
        system_messages, groups = self._split_system_and_rounds(messages)
        if len(groups) < 2:
            return None
        drop_count = max(1, len(groups) // 5)
        drop_count = min(drop_count, len(groups) - 1)
        marker = {"role": "user", "content": self._build_truncated_history_marker(transcript_path)}
        return [*system_messages, marker, *self._flatten(groups[drop_count:])]

    def restore_recent_files(self, session: dict[str, Any]) -> list[dict[str, Any]]:
        """重新读取最近成功读取过的文件，恢复精确代码上下文。

        历史 ``read_code`` 结果可能在后续 ``edit_code`` 后过期，因此必须
        从磁盘重新读取。恢复范围限制在项目根目录下，防止旧 session 中
        的异常路径导致越界读取。
        """
        calls = session.get("tool_calls", [])
        if not isinstance(calls, list):
            return []

        project_root = Path(self.config.project.root).resolve()
        max_files = max(0, self.config.compact.restore_max_files)
        per_file_budget = max(0, self.config.compact.restore_max_chars_per_file)
        remaining_budget = max(0, self.config.compact.restore_total_chars)
        restored: list[dict[str, Any]] = []
        seen: set[str] = set()

        for call in reversed(calls):
            if len(restored) >= max_files or remaining_budget <= 0:
                break
            if not isinstance(call, dict) or call.get("name") != "read_code":
                continue
            result = call.get("result")
            if not isinstance(result, dict) or not result.get("success"):
                continue
            raw_path = self._read_code_path(call, result)
            if not raw_path:
                continue
            try:
                path = Path(raw_path)
                if not path.is_absolute():
                    path = project_root / path
                resolved = path.resolve()
                resolved.relative_to(project_root)
            except (OSError, ValueError):
                continue
            dedup_key = str(resolved).lower()
            if dedup_key in seen or not resolved.is_file():
                continue
            try:
                content = resolved.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            allowed_chars = min(per_file_budget, remaining_budget)
            if allowed_chars <= 0:
                break
            truncated = len(content) > allowed_chars
            if truncated:
                # 极小预算下标记本身也要截断，确保总字符数严格不超预算。
                marker = RESTORED_FILE_TRUNCATION_MARKER[:allowed_chars]
                head_chars = max(0, allowed_chars - len(marker))
                content = content[:head_chars] + marker
            remaining_budget -= len(content)
            restored.append({"path": str(resolved), "content": content, "truncated": truncated})
            seen.add(dedup_key)
        return restored

    def _summarize_conversation(
        self,
        messages: list[dict[str, Any]],
        *,
        transcript_path: str,
    ) -> tuple[str, int, dict[str, Any]]:
        """调用无工具 LLM 总结旧轮次；PTL 时逐步截断旧历史。"""
        if self.llm_client is None:
            raise RuntimeError("llm_unavailable")

        current_messages = list(messages)
        ptl_retry_count = 0
        while True:
            summary_messages = [*current_messages, {"role": "user", "content": SUMMARY_PROMPT}]
            response = self.llm_client.chat(
                summary_messages,
                tools=None,
                max_tokens=self.config.compact.summary_max_output_tokens,
            )
            if response.success:
                summary = str(response.data.get("content", "")).strip()
                if not summary:
                    raise RuntimeError("compact_summary_empty")
                usage = response.data.get("token_usage")
                return summary, ptl_retry_count, usage if isinstance(usage, dict) else {}

            if self._is_prompt_too_long(response) and ptl_retry_count < self.config.compact.max_ptl_retries:
                truncated = self.truncate_head_for_ptl_retry(current_messages, transcript_path)
                if truncated is not None:
                    current_messages = truncated
                    ptl_retry_count += 1
                    continue
            raise RuntimeError(f"compact_summary_failed: {response.stderr_summary}")

    def _fit_compacted_context(
        self,
        *,
        rebuilt_system_prompt: str,
        summary: str,
        slim_session_state: dict[str, Any],
        recent_groups: list[list[dict[str, Any]]],
        restored_files: list[dict[str, Any]],
        transcript_path: str,
        tools: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]], list[dict[str, Any]]]:
        """按优先级缩减恢复内容，尽量将重建上下文压到阻断阈值以下。"""
        fitted_groups = list(recent_groups)
        fitted_files = list(restored_files)
        while True:
            messages = self._build_compacted_messages(
                rebuilt_system_prompt=rebuilt_system_prompt,
                summary=summary,
                slim_session_state=slim_session_state,
                recent_groups=fitted_groups,
                restored_files=fitted_files,
                transcript_path=transcript_path,
            )
            if self.estimator.estimate_context(messages, tools) < self.blocking_threshold:
                return messages, fitted_groups, fitted_files
            if len(fitted_groups) > 1:
                # 先移除最旧的保留轮次，始终留下模型刚刚完成的最后一轮。
                fitted_groups = fitted_groups[1:]
                continue
            if fitted_files:
                # 文件按最近优先排列，因此从末尾移除最旧文件。
                fitted_files.pop()
                continue
            return messages, fitted_groups, fitted_files

    def _build_compacted_messages(
        self,
        *,
        rebuilt_system_prompt: str,
        summary: str,
        slim_session_state: dict[str, Any],
        recent_groups: list[list[dict[str, Any]]],
        restored_files: list[dict[str, Any]],
        transcript_path: str,
    ) -> list[dict[str, Any]]:
        """构造摘要边界消息，并在其后追加最近完整轮次。"""
        boundary = {
            "type": "legacy_full_compact_boundary",
            "summary": summary,
            "current_state": slim_session_state,
            "restored_files": restored_files,
            "transcript_path": transcript_path,
            "instruction": "直接继续修复任务。需要补充证据时调用工具。不要复述摘要，不要向用户提问。",
        }
        return [
            {"role": "system", "content": rebuilt_system_prompt},
            {"role": "user", "content": json.dumps(boundary, ensure_ascii=False, default=str)},
            *self._flatten(recent_groups),
        ]

    def _split_system_and_rounds(
        self,
        messages: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]]]:
        """拆分开头的 system prompt，并移除上一次 PTL 重试标记。"""
        system_messages: list[dict[str, Any]] = []
        remaining = list(messages)
        while remaining and remaining[0].get("role") == "system":
            system_messages.append(remaining.pop(0))
        if remaining and str(remaining[0].get("content", "")).startswith(TRUNCATED_HISTORY_MARKER):
            remaining.pop(0)
        return system_messages, self.group_messages_by_api_round(remaining)

    def _select_recent_groups(self, groups: list[list[dict[str, Any]]]) -> list[list[dict[str, Any]]]:
        """选择最后 N 个协议完整轮次。

        正常运行时消息天然成对。这里仍然做一次校验，避免损坏的恢复数据
        将孤立 tool 消息带进新的活动上下文。
        """
        keep_count = max(1, self.config.compact.keep_recent_rounds)
        selected = groups[-keep_count:]
        while selected and not self._has_valid_tool_pairs(self._flatten(selected)):
            selected = selected[1:]
        return selected

    def _has_valid_tool_pairs(self, messages: list[dict[str, Any]]) -> bool:
        """检查所有保留的 tool_calls 是否都有结果，且 tool 结果都有来源。"""
        pending_call_ids: set[str] = set()
        for message in messages:
            if message.get("role") == "assistant":
                tool_calls = message.get("tool_calls")
                if isinstance(tool_calls, list):
                    for tool_call in tool_calls:
                        if isinstance(tool_call, dict) and tool_call.get("id"):
                            pending_call_ids.add(str(tool_call["id"]))
            elif message.get("role") == "tool":
                tool_call_id = str(message.get("tool_call_id") or "")
                if not tool_call_id or tool_call_id not in pending_call_ids:
                    return False
                pending_call_ids.remove(tool_call_id)
        return not pending_call_ids

    def _record_failure(
        self,
        *,
        session: dict[str, Any],
        state: LegacyCompactionState,
        reason: str,
        tokens_before: int,
        tokens_after: int | None = None,
        increment_failure: bool = True,
    ) -> LegacyCompactionResult:
        """记录 compact 失败，并在主调用也接近极限时要求阻断。"""
        if increment_failure:
            state.consecutive_failures += 1
        state.tokens_before = tokens_before
        state.tokens_after = tokens_after if tokens_after is not None else tokens_before
        state.last_error = reason
        blocked = state.tokens_after >= self.blocking_threshold
        result = LegacyCompactionResult(
            blocked=blocked,
            reason=reason,
            tokens_before=tokens_before,
            tokens_after=state.tokens_after,
        )
        self._store_state(session, state)
        self._store_result(session, result)
        return result

    def _load_state(self, session: dict[str, Any]) -> LegacyCompactionState:
        """从 session 恢复状态；损坏的旧值按初始状态处理。"""
        raw = session.get("legacy_compaction_state")
        if isinstance(raw, dict):
            try:
                return LegacyCompactionState.model_validate(raw)
            except ValueError:
                pass
        return LegacyCompactionState()

    def _store_state(self, session: dict[str, Any], state: LegacyCompactionState) -> None:
        session["legacy_compaction_state"] = state.model_dump(mode="json")

    def _store_result(self, session: dict[str, Any], result: LegacyCompactionResult) -> None:
        # 活动 messages 可能很大，不重复写入 session；完整审计已经另有存储。
        session["last_legacy_compaction"] = result.model_dump(mode="json", exclude={"messages"})

    def _persist_summary(self, bug_id: str, compact_count: int, summary: str) -> Path:
        """将摘要保存为可供人工排查的 Markdown 文件。"""
        safe_bug_id = re.sub(r"[^A-Za-z0-9._-]+", "-", bug_id).strip("-") or "bug"
        compact_dir = Path(self.config.session.root_dir) / safe_bug_id / "compactions"
        compact_dir.mkdir(parents=True, exist_ok=True)
        path = compact_dir / f"compact-{compact_count}.md"
        path.write_text(self.sanitizer.sanitize(summary), encoding="utf-8")
        return path

    def _build_slim_session_state(self, session: dict[str, Any]) -> dict[str, Any]:
        """提取摘要边界中真正有用的当前状态，避免重新注入完整审计历史。"""
        keys = (
            "status",
            "last_error",
            "last_tool_result",
            "compile_result",
            "test_result",
            "rollback_result",
            "test_generation_result",
            "pr_url",
            "agent_branch",
            "base_branch",
            "denied_commands",
            "legacy_compaction_state",
            "transcript_path",
        )
        return {
            key: self._limit_value(session[key])
            for key in keys
            if key in session and session[key] not in (None, "", [], {})
        }

    def _limit_value(self, value: Any, max_chars: int = 4_000) -> Any:
        """限制状态附件大小；完整数据仍然保留在 session 审计记录中。"""
        serialized = json.dumps(value, ensure_ascii=False, default=str)
        if len(serialized) <= max_chars:
            return value
        return serialized[:max_chars] + "...[状态内容已截断]"

    def _read_code_path(self, call: dict[str, Any], result: dict[str, Any]) -> str:
        arguments = call.get("arguments")
        if isinstance(arguments, dict) and arguments.get("path"):
            return str(arguments["path"])
        data = result.get("data")
        if isinstance(data, dict) and data.get("path"):
            return str(data["path"])
        return ""

    def _is_prompt_too_long(self, response: ToolResult) -> bool:
        """兼容不同供应商的 Prompt Too Long 错误文本。"""
        message = response.stderr_summary.lower()
        markers = (
            "prompt too long",
            "context length",
            "context_length_exceeded",
            "maximum context",
            "too many tokens",
        )
        return any(marker in message for marker in markers)

    def _build_truncated_history_marker(self, transcript_path: str) -> str:
        """Tell the summarizer where exact early details remain available."""
        readable_path = transcript_path or "[transcript unavailable]"
        return (
            f"{TRUNCATED_HISTORY_MARKER}\n"
            "If you need specific details from before compaction\n"
            "(like exact code snippets, error messages, or content you generated),\n"
            f"read the full transcript at: {readable_path}"
        )

    def _usage_value(self, usage: dict[str, Any], *keys: str) -> int:
        for key in keys:
            value = usage.get(key)
            if isinstance(value, int):
                return value
        return 0

    def _flatten(self, groups: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
        return [message for group in groups for message in group]
