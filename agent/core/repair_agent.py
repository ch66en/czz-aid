from __future__ import annotations

"""实现带粗流程约束的修复代理运行时。"""

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

import requests

from agent.config import AppConfig
from agent.core.compact_transcript import CompactTranscript
from agent.core.legacy_full_compactor import LegacyFullCompactor
from agent.core.permission_guard import PermissionGuard
from agent.core.test_generation_agent import TestGenerationAgent
from agent.core.task_manager import TaskManager
from agent.core.tool_registry import ToolRegistry
from agent.ingestion.sanitizer import Sanitizer
from agent.llm.openai_compatible_client import OpenAICompatibleClient
from agent.models import BugEvent, RepairTask, TaskStatus, ToolResult, ToolSpec
from agent.rag.knowledge_service import KnowledgeService
from agent.rag.models import RagRepairContext, RagStatus
from agent.storage.session_store import SessionStore
from agent.storage.skill_store import SkillStore
from agent.tools.base import PermissionType
from agent.tools.ast_symbols_tool import AstSymbolsTool
from agent.tools.apply_test_patch import ApplyTestPatchTool
from agent.tools.compile_tool import RunCompileTool
from agent.tools.edit_code import EditCodeTool
from agent.tools.feishu_tool import FeishuTool
from agent.tools.git_diff import GitDiffTool
from agent.tools.read_code import ReadCodeTool
from agent.tools.read_symbol_at_tool import ReadSymbolAtTool
from agent.tools.run_command import RunCommandTool
from agent.tools.search_code import SearchCodeTool
from agent.tools.search_project_doc import SearchProjectDocTool
from agent.tools.search_skill import SearchSkillTool
from agent.tools.test_tool import RunTestTool
import agent.ui as ui


@dataclass(slots=True)
class RepairRunResult:
    """表示一次修复运行的最终结果。"""

    success: bool
    status: str
    message: str
    task: RepairTask | None = None
    last_result: ToolResult | None = None
    prompt_template: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)


class RepairAgent:
    """负责按固定粗流程驱动 LLM 工具调用与修复闭环。"""

    def __init__(
        self,
        config: AppConfig,
        registry: ToolRegistry,
        permission_guard: PermissionGuard,
        task_manager: TaskManager,
        session_store: SessionStore,
        skill_store: SkillStore,
        llm_client: OpenAICompatibleClient | None = None,
        knowledge_service: KnowledgeService | None = None,
    ) -> None:
        """初始化修复代理所依赖的各项组件。"""
        self.config = config
        self.registry = registry
        self.permission_guard = permission_guard
        self.task_manager = task_manager
        self.session_store = session_store
        self.skill_store = skill_store
        self.llm_client = llm_client
        self.knowledge_service = knowledge_service
        self.sanitizer = Sanitizer()
        self.compact_transcript = CompactTranscript(config=config, sanitizer=self.sanitizer)
        self.legacy_compactor = LegacyFullCompactor(
            config=config,
            llm_client=llm_client,
            sanitizer=self.sanitizer,
            transcript=self.compact_transcript,
        )
        self._ensure_core_tools()
        apply_test_patch_tool = self.registry.get("apply_test_patch")
        self.test_generation_agent = TestGenerationAgent(
            config=config,
            llm_client=llm_client,
            apply_tool=apply_test_patch_tool if isinstance(apply_test_patch_tool, ApplyTestPatchTool) else None,
        )

    def _ensure_core_tools(self) -> None:
        """确保核心工具已注册到工具注册表。"""
        tools = [
            AstSymbolsTool(),
            ReadSymbolAtTool(),
            ReadCodeTool(self.config),
            SearchCodeTool(),
            EditCodeTool(self.config),
            ApplyTestPatchTool(self.config),
            RunCommandTool(self.config),
            GitDiffTool(),
            RunCompileTool(self.config),
            RunTestTool(self.config),
        ]
        if self.config.rag.dynamic_tools_enabled:
            tools.extend(
                [
                    SearchSkillTool(self.config, self.knowledge_service),
                    SearchProjectDocTool(self.config, self.knowledge_service),
                ]
            )
        for tool in tools:
            if self.registry.get(tool.spec.name) is None:
                self.registry.register(tool)

    def build_prompt_template(
        self,
        bug_event: BugEvent,
        session: dict[str, Any],
        rag_context: RagRepairContext | dict[str, Any] | None = None,
    ) -> str:
        """构造修复代理系统提示词模板。"""
        frame_contexts = session.get("frame_contexts", []) if isinstance(session, dict) else []
        # session["tool_calls"] 是完整审计日志，恢复旧任务时可能非常大。
        # 系统提示词只注入继续修复所需的精简状态，完整记录仍保留在 SessionStore。
        prompt_session = self._build_slim_session_view(session)
        bug_summary = bug_event.model_dump(exclude={"traceback"})
        bug_summary["traceback_omitted"] = bool(bug_event.traceback)
        rules = [
            "Use only the available function tools; do not describe tool calls in plain text.",
            "Call exactly one function tool per turn.",
            "Current source code and test results are the highest-priority facts.",
            "Use bug_event.frames and frame_contexts to locate the failure; full traceback is omitted to avoid repeated stack noise.",
            "Prefer the top business frame as the first repair target unless evidence points elsewhere.",
            "rag_context.hard_constraints contain approved business constraints; preserve them unless current evidence shows a conflict.",
            "rag_context.soft_hints are historical experience, not current-source facts. Verify them with read_code before editing.",
            "Do not turn rag_context.avoid_patterns into a recommended patch.",
            "When rag_context.confidence is low, treat all RAG guidance as weak hints.",
            "When rag_context.conflicts is non-empty, preserve the conflict in the repair explanation for human review.",
            "CRITICAL: frame_contexts may contain pre-extracted source code for some business frames as reference, but you should still call read_code to gather full context and evidence before repairing. When calling read_code or search_code, always use the full filePath from frame_contexts or bug_event.frames — never use bare filenames like 'OrderService.java'.",
            "Use search_code for project-scoped Java keyword searches; its root is enforced by runtime.",
            "edit_code content must be a unified diff/patch, not a full file rewrite.",
            "After a successful edit, finish_patch triggers compile and test; do not skip them.",
            "After a valid source edit, runtime may generate a separate regression test patch before compile/test.",
            "Return finish_patch only after a code edit has succeeded.",
        ]
        if self.config.rag.dynamic_tools_enabled:
            rules.extend(
                [
                    "Use search_project_doc only when the pre-retrieved context is insufficient.",
                    "Use search_skill only when additional historical precedent is required.",
                ]
            )
        context_value = rag_context or session.get("rag_context") or RagRepairContext()
        if hasattr(context_value, "model_dump"):
            context_value = context_value.model_dump(mode="json")
        prompt = {
            "role": "You are an automated Java repair agent. Use native function tools to inspect and repair Java bugs.",
            "rules": rules,
            "terminal_tools": {
                "finish_patch": "Call after a successful edit_code when the patch is ready for compile/test.",
            },
            "project": {"name": bug_event.project, "root": self.config.project.root},
            "bug_event": bug_summary,
            "frame_contexts": frame_contexts,
            "rag_context": context_value,
            "session": prompt_session,
        }
        return json.dumps(prompt, ensure_ascii=False, default=str)

    def repair(self, bug_id: str) -> RepairRunResult:
        """执行最多三轮的自动修复流程。"""
        task = self.task_manager.create_task(bug_id)
        self.task_manager.update_status(bug_id, TaskStatus.RUNNING)
        bug_event = self._load_bug_event(bug_id)
        self._save_bug_event(bug_event)
        session = self._load_session(bug_id)
        frame_contexts = session.get("frame_contexts", []) if isinstance(session, dict) else []
        if frame_contexts:
            session = {**session, "frame_contexts": frame_contexts}
        rag_context, rag_status = self._pre_retrieve_for_bug(bug_event, session)
        session["rag_context"] = rag_context.model_dump(mode="json")
        session["rag_status"] = rag_status.model_dump(mode="json")
        self._save_session(bug_id, session)
        prompt_template = self.build_prompt_template(bug_event, session, rag_context)
        history: list[dict[str, Any]] = []
        llm_messages: list[dict[str, Any]] = [{"role": "system", "content": prompt_template}]
        self._append_transcript_message(bug_id, llm_messages[0], source="repair_start", session=session)
        last_result: ToolResult | None = None

        ui.step(f"Starting repair  bug_id={bug_id}  exception={bug_event.exception_type}")
        for attempt in range(1, self.config.agent.max_retry + 1):
            ui.attempt(attempt, self.config.agent.max_retry, bug_id)
            modified = False
            test_generated = False
            invalid_output_retries = 0
            finish_without_patch_rejections = 0
            while True:
                # Compact 只收缩下一次主模型调用使用的 llm_messages。
                # history 和 session["tool_calls"] 仍然完整保留，避免影响回滚、
                # PR 文件收集以及后续人工审计。
                compaction = self.legacy_compactor.compact_if_needed(
                    bug_id=bug_id,
                    messages=llm_messages,
                    session=session,
                    rebuilt_system_prompt=prompt_template,
                    tools=self._openai_tools(),
                )
                if compaction.compacted:
                    llm_messages[:] = compaction.messages
                    self._save_session(bug_id, session)
                    ui.info(
                        "Legacy compact completed  "
                        f"tokens={compaction.tokens_before}->{compaction.tokens_after}  "
                        f"dropped_rounds={compaction.dropped_round_count}"
                    )
                elif compaction.reason not in {"disabled", "llm_unavailable", "below_threshold"}:
                    # 失败状态也要持久化，使熔断器在下一轮工具调用后仍然生效。
                    self._save_session(bug_id, session)
                    ui.warning(f"Legacy compact skipped  reason={compaction.reason}")

                if compaction.blocked:
                    last_result = ToolResult(
                        tool="legacy_full_compact",
                        success=False,
                        exit_code=1,
                        stdout_summary="",
                        stderr_summary=compaction.reason,
                        data=compaction.model_dump(mode="json", exclude={"messages"}),
                        artifacts=[],
                    )
                    history.append({"tool": "legacy_full_compact", "result": last_result.model_dump()})
                    session["last_error"] = last_result.model_dump()
                    self.task_manager.update_status(bug_id, TaskStatus.FAILED)
                    self._save_session(bug_id, session)
                    self._send_feishu_help(bug_event, session, last_result)
                    ui.error(f"Legacy compact blocked main LLM call  reason={compaction.reason}")
                    return RepairRunResult(
                        False,
                        "failed",
                        "context compact failed at blocking threshold",
                        task=task,
                        last_result=last_result,
                        prompt_template=prompt_template,
                        history=history,
                    )

                spinner = ui.Spinner("Thinking")
                spinner.start()
                try:
                    action = self._ask_llm(llm_messages, history, bug_event, session)
                finally:
                    spinner.stop()
                history.append(action)
                tool_name = str(action.get("tool", ""))
                ui.tool_call(tool_name or "unknown", action.get("reason", ""))
                if tool_name == "__invalid_llm_output__":
                    last_result = ToolResult(
                        tool="llm_chat",
                        success=False,
                        exit_code=1,
                        stdout_summary="",
                        stderr_summary=str(action.get("reason", "invalid llm output")),
                        data={"action": action},
                        artifacts=[],
                    )
                    session["last_error"] = last_result.model_dump()
                    self._append_session_tool_call(session, action, last_result)
                    self._save_session(bug_id, session)
                    history.append(
                        {
                            "tool": "llm_chat",
                            "result": last_result.model_dump(),
                            "feedback": "Rejected: native function call required. Use one available tool call, not prose.",
                        }
                    )
                    self._append_tool_result_message(llm_messages, action, last_result, bug_id=bug_id)
                    invalid_output_retries += 1
                    if invalid_output_retries <= 2:
                        ui.warning("Invalid LLM output rejected; requesting native tool_call again")
                        continue
                    break
                if tool_name == "finish_patch":
                    if not modified:
                        last_result = ToolResult(tool="finish_patch", success=False, exit_code=1, stdout_summary="no patch produced", stderr_summary="", data={}, artifacts=[])
                        session["last_error"] = last_result.model_dump()
                        self._append_session_tool_call(session, action, last_result)
                        self._save_session(bug_id, session)
                        history.append(
                            {
                                "tool": "finish_patch",
                                "result": last_result.model_dump(),
                                "feedback": "Rejected: finish_patch requires a successful edit_code in the current attempt. Use edit_code to produce a patch.",
                            }
                        )
                        self._append_tool_result_message(llm_messages, action, last_result, bug_id=bug_id)
                        finish_without_patch_rejections += 1
                        if finish_without_patch_rejections <= 2:
                            ui.warning("finish_patch rejected: no successful edit_code")
                            continue
                        break
                    compile_result = self._run_compile()
                    history.append({"tool": "RunCompileTool", "result": compile_result.model_dump()})
                    session["compile_result"] = compile_result.model_dump()
                    self._save_session(bug_id, session)
                    ui.compile_result(compile_result.success, compile_result.exit_code)
                    if not compile_result.success:
                        rollback_result = self._rollback_git_changes(history, bug_event)
                        history.append({"tool": "Rollback", "result": rollback_result.model_dump()})
                        session["rollback_result"] = rollback_result.model_dump()
                        self._save_session(bug_id, session)
                        last_result = compile_result
                        break
                    test_result = self._run_test()
                    history.append({"tool": "RunTestTool", "result": test_result.model_dump()})
                    session["test_result"] = test_result.model_dump()
                    self._save_session(bug_id, session)
                    ui.test_result(test_result.success, test_result.exit_code)
                    if not test_result.success:
                        rollback_result = self._rollback_git_changes(history, bug_event)
                        history.append({"tool": "Rollback", "result": rollback_result.model_dump()})
                        session["rollback_result"] = rollback_result.model_dump()
                        self._save_session(bug_id, session)
                        last_result = test_result
                        break
                    pr_result = self._create_pr(task, bug_event, history)
                    history.append({"tool": "CreatePR", "result": pr_result.model_dump()})
                    session["create_pr_result"] = pr_result.model_dump()
                    self._save_session(bug_id, session)
                    if not pr_result.success:
                        last_result = pr_result
                        self.task_manager.update_status(bug_id, TaskStatus.FAILED)
                        self._send_feishu_help(bug_event, session, last_result)
                        ui.error(f"Create PR failed  bug_id={bug_id}  error={pr_result.stderr_summary}")
                        return RepairRunResult(False, "failed", "create pr failed", task=task, last_result=pr_result, prompt_template=prompt_template, history=history)
                    pr_url = str(pr_result.data.get("pr_url") or pr_result.stdout_summary)
                    agent_branch = str(pr_result.data.get("branch") or task.agent_branch or self._repair_branch_name(bug_event.bug_id))
                    base_branch = str(pr_result.data.get("base_branch") or task.base_branch or self.config.project.default_branch)
                    task.pr_url = pr_url
                    task.agent_branch = agent_branch
                    task.base_branch = base_branch
                    session = {
                        **session,
                        "agent_branch": agent_branch,
                        "base_branch": base_branch,
                        "pr_url": pr_url,
                        "status": "passed",
                    }
                    review_result = self._send_feishu_review_request(task, bug_event, session, pr_result, compile_result, test_result)
                    if self.config.agent.review_required:
                        task.status = TaskStatus.REVIEWING
                        self.task_manager.update_status(bug_id, TaskStatus.REVIEWING)
                        session["status"] = "reviewing"
                        self._save_session(bug_id, session)
                        ui.review_requested(pr_url)
                        return RepairRunResult(True, "reviewing", "repair succeeded; review requested", task=task, last_result=test_result, prompt_template=prompt_template, history=history)
                    task.status = TaskStatus.PASSED
                    self.task_manager.update_status(bug_id, TaskStatus.PASSED)
                    self._save_session(bug_id, session)
                    ui.pr_created(pr_url)
                    return RepairRunResult(True, "passed", "repair succeeded", task=task, last_result=test_result, prompt_template=prompt_template, history=history)

                tool = self.registry.get(tool_name.lower()) or self.registry.get(tool_name)
                if tool is None:
                    last_result = ToolResult(tool=tool_name or "unknown", success=False, exit_code=1, stdout_summary="", stderr_summary=f"tool not found: {tool_name}", data={}, artifacts=[])
                    history.append({"tool": tool_name or "unknown", "result": last_result.model_dump()})
                    session["last_error"] = last_result.model_dump()
                    self._append_session_tool_call(session, action, last_result)
                    self._save_session(bug_id, session)
                    self._append_tool_result_message(llm_messages, action, last_result, bug_id=bug_id)
                    continue

                arguments = self._prepare_tool_arguments(tool.spec.name, action.get("arguments", {}), bug_event)
                action["arguments"] = arguments
                allowed, reason = self.permission_guard.can_execute(tool.spec, self.permission_guard.build_context(tool.permission), arguments)
                if not allowed:
                    last_result = ToolResult(tool=tool.spec.name, success=False, exit_code=1, stdout_summary="", stderr_summary=reason, data={"arguments": arguments, "tool": tool.spec.name}, artifacts=[])
                    history.append({"tool": tool.spec.name, "result": last_result.model_dump()})
                    session["last_error"] = last_result.model_dump()
                    denied_commands = session.get("denied_commands", [])
                    if not isinstance(denied_commands, list):
                        denied_commands = []
                    denied_entry = {
                        "tool": tool.spec.name,
                        "reason": reason,
                        "arguments": arguments,
                        "action": action,
                    }
                    denied_commands.append(denied_entry)
                    session["denied_commands"] = denied_commands
                    self._append_session_tool_call(session, action, last_result)
                    self._save_session(bug_id, session)
                    self._notify_whitelist_denial(bug_event, session, denied_entry)
                    ui.denied(tool.spec.name, reason)
                    self._append_tool_result_message(llm_messages, action, last_result, bug_id=bug_id)
                    continue

                result = tool.run(arguments)
                history.append({"tool": tool.spec.name, "result": result.model_dump()})
                last_result = result
                session["last_tool_result"] = result.model_dump()
                self._append_session_tool_call(session, action, result)
                self._save_session(bug_id, session)
                self._append_tool_result_message(llm_messages, action, result, bug_id=bug_id)
                if result.success:
                    ui.success(f"{tool.spec.name}  exit_code={result.exit_code}")
                else:
                    ui.error(f"{tool.spec.name}  exit_code={result.exit_code}")
                if tool.spec.name == "edit_code" and result.success and self._is_valid_patch(result, session):
                    modified = True
                    if not test_generated:
                        test_generated = True
                        self._generate_regression_test(bug_event, session, result, history, bug_id)
                elif tool.spec.name == "edit_code" and result.success:
                    session["last_error"] = {"tool": "edit_code", "error": "invalid patch target"}
                    self._save_session(bug_id, session)
                    ui.warning("Invalid patch target")

                # 继续在当前轮次中等待 finish_patch，直到进入编译/测试阶段。
                continue

            # 当前轮次结束，但测试失败后才算一轮失败，进入下一轮。
            continue

        self.task_manager.update_status(bug_id, TaskStatus.FAILED)
        self._send_feishu_help(bug_event, session, last_result)
        ui.error(f"Auto repair exhausted  bug_id={bug_id}")
        return RepairRunResult(False, "failed", "auto repair exhausted", task=task, last_result=last_result, prompt_template=prompt_template, history=history)

    def _prepare_tool_arguments(self, tool_name: str, raw_arguments: Any, bug_event: BugEvent) -> dict[str, Any]:
        """Normalize tool arguments with project-scoped defaults."""
        arguments = raw_arguments if isinstance(raw_arguments, dict) else {}
        prepared = dict(arguments)
        if tool_name == "search_code":
            project_root = self._project_root_for(bug_event)
            if project_root:
                prepared["root"] = project_root
        return prepared

    def _project_root_for(self, bug_event: BugEvent) -> str:
        """Return the configured root for the current bug event project."""
        configured_project = str(self.config.project.name)
        configured_root = str(self.config.project.root)
        if bug_event.project == configured_project and configured_root:
            return configured_root
        return configured_root

    def _load_bug_event(self, bug_id: str) -> BugEvent:
        """从会话或默认值中恢复 BugEvent。"""
        raw = self.session_store.get(f"bug_event:{bug_id}")
        if isinstance(raw, dict):
            return BugEvent.model_validate(raw)
        return BugEvent(
            bug_id=bug_id,
            source="unknown",
            project=self.config.project.name,
            title="",
            exception_type="UnknownError",
            message="",
            traceback="",
            fingerprint=bug_id,
        )

    def _load_session(self, bug_id: str) -> dict[str, Any]:
        """加载当前修复会话。"""
        session = self.session_store.get(bug_id)
        return session if isinstance(session, dict) else {}

    def _save_session(self, bug_id: str, session: dict[str, Any]) -> None:
        """保存脱敏后的会话上下文。"""
        sanitized_session = json.loads(self.sanitizer.sanitize(json.dumps(session, ensure_ascii=False)))
        self.session_store.put(bug_id, sanitized_session)

    def _save_bug_event(self, bug_event: BugEvent) -> None:
        """把 BugEvent 写入会话存储，供后续轮次读取。"""
        self.session_store.put(f"bug_event:{bug_event.bug_id}", bug_event.model_dump())

    def _build_slim_session_view(self, session: dict[str, Any]) -> dict[str, Any]:
        """提取系统提示词真正需要的 session 状态，避免重复注入完整审计历史。"""
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
            "rag_status",
        )
        slim: dict[str, Any] = {}
        for key in keys:
            value = session.get(key)
            if value in (None, "", [], {}):
                continue
            if key == "denied_commands" and isinstance(value, list):
                value = value[-3:]
            slim[key] = self._limit_prompt_value(value)
        return slim

    def _limit_prompt_value(self, value: Any, max_chars: int = 4_000) -> Any:
        """限制单项状态大小；完整内容仍然保存在 session 审计数据中。"""
        serialized = json.dumps(value, ensure_ascii=False, default=str)
        if len(serialized) <= max_chars:
            return value
        return serialized[:max_chars] + "...[状态内容已截断]"

    def _pre_retrieve_for_bug(self, bug_event: BugEvent, session: dict[str, Any]) -> tuple[RagRepairContext, RagStatus]:
        if self.knowledge_service is None or not self.config.rag.enabled:
            return RagRepairContext(missing_info=["RAG is disabled or unavailable."]), RagStatus(status="disabled")
        try:
            return self.knowledge_service.pre_retrieve_for_bug(bug_event, session)
        except Exception as exc:
            message = " ".join(self.sanitizer.sanitize(str(exc)).split())[:240]
            ui.warning(f"Repair RAG unavailable  error={message}")
            return (
                RagRepairContext(missing_info=["Repair-time RAG failed; continue with current source evidence."]),
                RagStatus(
                    status="failed",
                    degraded_stages=["pre_retrieve"],
                    reasons=[message],
                    fallback_strategies=["empty_rag_context"],
                ),
            )

    def _ask_llm(self, messages: list[dict[str, Any]], history: list[dict[str, Any]], bug_event: BugEvent, session: dict[str, Any]) -> dict[str, Any]:
        """请求 LLM 输出下一步动作。"""
        if self.llm_client is None:
            if not any(item.get("tool") == "edit_code" for item in history):
                target_path = self._pick_patch_target(bug_event, session)
                content = self._build_patch_content(target_path)
                if not content:
                    return {"tool": "finish_patch", "arguments": {}, "reason": f"no local patch rule for {target_path}"}
                return {"tool": "edit_code", "arguments": {"path": target_path, "content": content}, "reason": "create patch"}
            return {"tool": "finish_patch", "arguments": {}, "reason": "done"}
        response = self.llm_client.chat(messages, tools=self._openai_tools(), tool_choice="auto")
        artifact_path = response.data.get("artifact_path") if isinstance(response.data, dict) else ""
        if artifact_path:
            ui.info(f"LLM call saved → {artifact_path}")
        if not response.success:
            return self._invalid_llm_action(f"llm call failed: {response.stderr_summary}")
        tool_calls = response.data.get("tool_calls") if isinstance(response.data, dict) else None
        content = response.data.get("content") if isinstance(response.data, dict) else ""
        if isinstance(tool_calls, list) and tool_calls:
            tool_call = tool_calls[0]
            message = self._assistant_tool_call_message(tool_call, str(content or ""))
            messages.append(message)
            self._append_transcript_message(bug_event.bug_id, message, source="assistant")
            return self._tool_call_to_action(tool_call)
        payload = content
        if isinstance(payload, str):
            message = {"role": "assistant", "content": payload}
            messages.append(message)
            self._append_transcript_message(bug_event.bug_id, message, source="assistant")
            parsed_payload = self._parse_llm_action_payload(payload)
            if parsed_payload is None:
                return self._invalid_llm_action("invalid llm output: expected a native function tool_call")
            return self._normalize_llm_action(parsed_payload)
        return self._invalid_llm_action("no llm output")

    def _openai_tools(self) -> list[dict[str, Any]]:
        """Convert registered tools plus virtual terminal actions to OpenAI function tools."""
        specs = [tool.spec for tool in self.registry.list_tools() if tool.spec.name != "apply_test_patch"]
        specs.extend(self._virtual_tool_specs())
        return [self._tool_spec_to_openai_tool(spec) for spec in specs]

    def _virtual_tool_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="finish_patch",
                description="Finish the repair after a successful edit_code. Runtime will compile, test, create a PR, and request review.",
                input_schema={
                    "type": "object",
                    "properties": {"reason": {"type": "string", "description": "Why the patch is ready for compile and test."}},
                    "required": ["reason"],
                    "additionalProperties": False,
                },
                permission="READ_ONLY",
                executor="virtual",
            ),
        ]

    def _tool_spec_to_openai_tool(self, spec: ToolSpec) -> dict[str, Any]:
        parameters = dict(spec.input_schema or {})
        if parameters.get("type") != "object":
            parameters = {"type": "object", "properties": {}, "additionalProperties": False}
        parameters.setdefault("properties", {})
        return {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": parameters,
            },
        }

    def _assistant_tool_call_message(self, tool_call: dict[str, Any], content: str = "") -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": content or None,
            "tool_calls": [
                {
                    "id": str(tool_call.get("id") or ""),
                    "type": str(tool_call.get("type") or "function"),
                    "function": dict(tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}),
                }
            ],
        }

    def _tool_call_to_action(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
        name = str(function.get("name") or "")
        raw_arguments = function.get("arguments") or "{}"
        try:
            arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
        except json.JSONDecodeError:
            return self._invalid_llm_action(f"invalid tool arguments JSON for {name}", call_id=str(tool_call.get("id") or ""))
        if not isinstance(arguments, dict):
            return self._invalid_llm_action(f"invalid tool arguments for {name}: expected object", call_id=str(tool_call.get("id") or ""))
        reason = str(arguments.get("reason") or arguments.get("evidence") or "native function call")
        return {"tool": name, "arguments": arguments, "reason": reason, "tool_call_id": str(tool_call.get("id") or "")}

    def _append_tool_result_message(
        self,
        messages: list[dict[str, Any]],
        action: dict[str, Any],
        result: ToolResult,
        *,
        bug_id: str = "",
    ) -> None:
        call_id = str(action.get("tool_call_id") or "")
        content = self._tool_result_content(result)
        if call_id:
            message = {"role": "tool", "tool_call_id": call_id, "content": content}
            messages.append(message)
            self._append_transcript_message(bug_id, message, source="tool")
            return
        message = {"role": "user", "content": content}
        messages.append(message)
        self._append_transcript_message(bug_id, message, source="tool")

    def _append_transcript_message(
        self,
        bug_id: str,
        message: dict[str, Any],
        *,
        source: str,
        session: dict[str, Any] | None = None,
    ) -> None:
        """Best-effort transcript persistence must not interrupt a repair run."""
        if not bug_id:
            return
        try:
            path = self.compact_transcript.append_message(bug_id, message, source=source)
        except OSError as exc:
            ui.warning(f"Compact transcript append failed  error={exc}")
            return
        if session is not None:
            session["transcript_path"] = str(path)

    def _tool_result_content(self, result: ToolResult) -> str:
        return json.dumps(result.model_dump(), ensure_ascii=False, default=str)

    def _append_session_tool_call(self, session: dict[str, Any], action: dict[str, Any], result: ToolResult) -> None:
        calls = session.get("tool_calls", [])
        if not isinstance(calls, list):
            calls = []
        calls.append(
            {
                "tool_call_id": str(action.get("tool_call_id") or ""),
                "name": str(action.get("tool") or ""),
                "arguments": action.get("arguments", {}) if isinstance(action.get("arguments", {}), dict) else {},
                "result": result.model_dump(),
            }
        )
        session["tool_calls"] = calls

    def _parse_llm_action_payload(self, payload: str) -> Any | None:
        """Parse a model response only when the entire payload is pure JSON."""
        text = payload.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    def _normalize_llm_action(self, payload: Any) -> dict[str, Any]:
        """Convert model output into exactly one executable action."""
        if isinstance(payload, dict):
            tool = payload.get("tool")
            arguments = payload.get("arguments")
            reason = payload.get("reason")
            if not isinstance(tool, str) or not tool.strip():
                return self._invalid_llm_action("invalid llm action: tool must be a non-empty string")
            if arguments is None:
                arguments = {}
            if not isinstance(arguments, dict):
                return self._invalid_llm_action("invalid llm action: arguments must be an object")
            if reason is None:
                reason = ""
            if not isinstance(reason, str):
                return self._invalid_llm_action("invalid llm action: reason must be a string")
            return {"tool": tool.strip(), "arguments": arguments, "reason": reason}
        return self._invalid_llm_action("invalid llm action: top-level JSON must be an object")

    def _invalid_llm_action(self, reason: str, call_id: str = "") -> dict[str, Any]:
        """Return a non-executable action used to fail the current LLM attempt."""
        action = {"tool": "__invalid_llm_output__", "arguments": {}, "reason": reason}
        if call_id:
            action["tool_call_id"] = call_id
        return action

    def _pick_patch_target(self, bug_event: BugEvent, session: dict[str, Any]) -> str:
        """从上下文中选择真实源码文件作为补丁目标。"""
        frame_contexts = session.get("frame_contexts", [])
        if isinstance(frame_contexts, list):
            for context in frame_contexts:
                if not isinstance(context, dict):
                    continue
                file_path = str(context.get("filePath", ""))
                if file_path and file_path.endswith(".java") and Path(file_path).exists():
                    return file_path

        for frame in bug_event.frames:
            resolved = self._resolve_project_source(frame.file_path)
            if resolved is not None:
                return str(resolved)

        project_root = Path(self.config.project.root)
        if project_root.exists():
            for candidate in project_root.rglob("*.java"):
                if candidate.name in bug_event.traceback:
                    return str(candidate)
        return str(project_root / "src/main/java")

    def _resolve_project_source(self, file_path: str) -> Path | None:
        path = Path(file_path)
        if path.is_absolute() and path.exists():
            return path

        project_root = Path(self.config.project.root)
        candidates: list[Path] = []
        if path.exists():
            candidates.append(path)
        if project_root.exists():
            candidates.append(project_root / file_path)
            candidates.extend(project_root.rglob(path.name))

        for candidate in candidates:
            if candidate.exists() and candidate.suffix.lower() == ".java":
                return candidate
        return None

    def _build_patch_content(self, target_path: str) -> str:
        """为默认离线模式生成最小 unified diff 补丁。"""
        path = Path(target_path)
        if path.name == "QuickSortWithBugLogFile.java":
            return self._build_quicksort_patch(path)
        return ""

    def _build_quicksort_patch(self, path: Path) -> str:
        if not path.exists():
            return ""

        pivot_line = ""
        log_line = ""
        for line in path.read_text(encoding="utf-8").splitlines():
            if re.search(r"\bint\s+pivot\s*=\s*arr\s*\[\s*right\s*\+\s*1\s*\]\s*;", line):
                pivot_line = line
            elif "log(" in line and "pivot" in line and re.search(r"\(\s*right\s*\+\s*1\s*\)", line):
                log_line = line

        if not pivot_line:
            return ""

        indent = pivot_line[: len(pivot_line) - len(pivot_line.lstrip())]
        lines = [
            "--- a/src/main/java/org/example/QuickSortWithBugLogFile.java",
            "+++ b/src/main/java/org/example/QuickSortWithBugLogFile.java",
            "@@",
            f"-{pivot_line}",
            f"+{indent}int pivot = arr[right];",
        ]
        if log_line:
            new_log_line = re.sub(r"\(\s*right\s*\+\s*1\s*\)", "right", log_line, count=1)
            lines.extend([f"-{log_line}", f"+{new_log_line}"])
        return "\n".join(lines)

    def _run_compile(self) -> ToolResult:
        """强制执行编译步骤。"""
        tool = self.registry.get("run_compile")
        if tool is None:
            return ToolResult(tool="run_compile", success=False, exit_code=1, stdout_summary="", stderr_summary="compile tool missing", data={}, artifacts=[])
        return tool.run({})

    def _run_test(self) -> ToolResult:
        """强制执行测试步骤。"""
        tool = self.registry.get("run_test")
        if tool is None:
            return ToolResult(tool="run_test", success=False, exit_code=1, stdout_summary="", stderr_summary="test tool missing", data={}, artifacts=[])
        return tool.run({})

    def _generate_regression_test(
        self,
        bug_event: BugEvent,
        session: dict[str, Any],
        edit_result: ToolResult,
        history: list[dict[str, Any]],
        bug_id: str,
    ) -> None:
        """Generate a regression test patch after a successful source repair."""
        result = self.test_generation_agent.generate_for_repair(
            bug_event=bug_event,
            session=session,
            edit_result=edit_result,
            history=history,
        )
        record: dict[str, Any] = {
            "success": result.success,
            "skipped": result.skipped,
            "message": result.message,
            "patch": result.patch or {},
        }
        if result.tool_result is not None:
            record["tool_result"] = result.tool_result.model_dump()
            history.append({"tool": result.tool_result.tool, "result": result.tool_result.model_dump()})
            session["last_tool_result"] = result.tool_result.model_dump()
        session["test_generation_result"] = record
        self._save_session(bug_id, session)
        if result.skipped:
            ui.info(f"Test generation skipped  reason={result.message}")
        elif result.success:
            ui.success("Regression test patch generated")
        else:
            ui.warning(f"Regression test patch rejected  reason={result.message}")

    def _is_valid_patch(self, result: ToolResult, session: dict[str, Any]) -> bool:
        """判断 edit_code 是否修改了真实业务源码。"""
        path = str((result.data or {}).get("path", ""))
        if not path:
            return False
        if not path.replace("\\", "/").lower().endswith(".java"):
            return False
        try:
            patch_path = Path(path).resolve()
            project_root = Path(self.config.project.root).resolve()
        except OSError:
            return False
        frame_contexts = session.get("frame_contexts", [])
        if isinstance(frame_contexts, list) and frame_contexts:
            for context in frame_contexts:
                if not isinstance(context, dict):
                    continue
                context_path = str(context.get("filePath", ""))
                try:
                    if Path(context_path).resolve() == patch_path:
                        return True
                except OSError:
                    if context_path.replace("\\", "/").lower() == path.replace("\\", "/").lower():
                        return True

        try:
            relative_path = patch_path.relative_to(project_root)
        except ValueError:
            return False
        relative_parts = [part.lower() for part in relative_path.parts]
        if relative_parts and relative_parts[0] in {"tmp", "temp"}:
            return False
        if self._is_project_java_source_path(relative_path):
            return True
        return not isinstance(frame_contexts, list) or not frame_contexts

    def _is_project_java_source_path(self, relative_path: Path) -> bool:
        """Return True for Java source or test source files inside a project."""
        parts = [part.lower() for part in relative_path.parts]
        for index in range(len(parts) - 2):
            if parts[index : index + 3] in (["src", "main", "java"], ["src", "test", "java"]):
                return True
        return False

    def _create_pr(self, task: RepairTask, bug_event: BugEvent, history: list[dict[str, Any]]) -> ToolResult:
        """Create a real branch, push it, and open a Gitee pull request."""
        project_root = Path(self.config.project.root).resolve()
        branch = self._repair_branch_name(bug_event.bug_id)
        base_branch = self.config.project.default_branch
        edited_paths = self._edited_paths_from_history(history, project_root)
        if not edited_paths:
            return self._pr_error("no edited files to commit", branch=branch)

        owner, repo = self._resolve_gitee_repo(project_root)
        token = self.config.gitee.token.strip()
        if not token or token == "your-gitee-token":
            return self._pr_error("missing gitee token", branch=branch, owner=owner, repo=repo)
        if not owner or not repo or owner == "your-owner" or repo == "your-repo":
            return self._pr_error("missing gitee owner/repo", branch=branch, owner=owner, repo=repo)

        commands = [
            ["git", "checkout", "-B", branch],
            ["git", "add", "-f", *[str(path.relative_to(project_root)) for path in edited_paths]],
            ["git", "commit", "-m", f"fix: auto repair {bug_event.bug_id}"],
            ["git", "push", "--force", "-u", "origin", branch],
        ]
        command_output: list[str] = []
        for command in commands:
            result = self._run_git(project_root, command)
            command_output.append(f"$ {' '.join(command)}\n{result.stdout}\n{result.stderr}".strip())
            if result.returncode != 0:
                if command[:2] == ["git", "commit"] and "nothing to commit" in f"{result.stdout}\n{result.stderr}".lower():
                    return self._pr_error("no git changes to commit", branch=branch, owner=owner, repo=repo, stdout="\n\n".join(command_output))
                return self._pr_error(f"git command failed: {' '.join(command)}", branch=branch, owner=owner, repo=repo, stdout="\n\n".join(command_output))

        pr_response = self._create_gitee_pull_request(owner, repo, branch, base_branch, bug_event)
        if not pr_response.success:
            pr_response.stdout_summary = "\n\n".join(command_output)
            return pr_response

        pr_url = str(pr_response.data.get("pr_url") or pr_response.stdout_summary)
        task.agent_branch = branch
        task.base_branch = base_branch
        self.session_store.put(f"pr:{task.id}", pr_url)
        session = self.session_store.get(task.bug_id)
        self.session_store.put(
            task.bug_id,
            {
                **(session if isinstance(session, dict) else {}),
                "agent_branch": branch,
                "base_branch": base_branch,
                "pr_url": pr_url,
            },
        )
        return ToolResult(
            tool="create_pr",
            success=True,
            exit_code=0,
            stdout_summary=pr_url,
            stderr_summary="",
            data={"pr_url": pr_url, "branch": branch, "base_branch": base_branch, "owner": owner, "repo": repo},
            artifacts=[],
        )

    def _run_git(self, cwd: Path, command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace", shell=False, check=False)

    def _is_git_tracked(self, file_path: Path, project_root: Path) -> bool:
        """检查文件是否已被 Git 跟踪。"""
        try:
            relative = file_path.resolve().relative_to(project_root.resolve())
        except ValueError:
            return False
        result = self._run_git(project_root, ["git", "ls-files", "--error-unmatch", str(relative)])
        return result.returncode == 0

    def _rollback_git_changes(self, history: list[dict[str, Any]], bug_event: BugEvent) -> ToolResult:
        """回滚本轮所有编辑：已跟踪文件用 git restore 恢复，未跟踪的新文件直接删除。"""
        project_root = Path(self.config.project.root).resolve()
        edited_paths = self._edited_paths_from_history(history, project_root)
        if not edited_paths:
            return ToolResult(tool="rollback", success=True, exit_code=0, stdout_summary="no files to rollback", stderr_summary="", data={}, artifacts=[])

        tracked: list[str] = []
        untracked: list[str] = []
        restored: list[str] = []
        removed: list[str] = []
        errors: list[str] = []

        for path in edited_paths:
            relative = str(path.resolve().relative_to(project_root))
            if self._is_git_tracked(path, project_root):
                tracked.append(relative)
            else:
                untracked.append(relative)

        if tracked:
            result = self._run_git(project_root, ["git", "restore", *tracked])
            if result.returncode == 0:
                restored.extend(str(project_root / rel) for rel in tracked)
            else:
                errors.append(f"git restore failed: {(result.stderr or '').strip()}")

        for rel in untracked:
            try:
                target = project_root / rel
                if target.exists():
                    target.unlink()
                    removed.append(str(target))
            except OSError as exc:
                errors.append(f"remove {rel} failed: {exc}")

        success = not errors
        summary_parts: list[str] = []
        if restored:
            summary_parts.append(f"restored {len(restored)} tracked file(s)")
        if removed:
            summary_parts.append(f"removed {len(removed)} untracked file(s)")
        if errors:
            summary_parts.append(f"{len(errors)} error(s)")

        ui.rollback(len(restored), len(removed))

        return ToolResult(
            tool="rollback",
            success=success,
            exit_code=0 if success else 1,
            stdout_summary="; ".join(summary_parts) or "nothing to rollback",
            stderr_summary="; ".join(errors),
            data={"tracked": tracked, "untracked": untracked, "restored": restored, "removed": removed, "errors": errors},
            artifacts=[str(p) for p in edited_paths],
        )

    def _edited_paths_from_history(self, history: list[dict[str, Any]], project_root: Path) -> list[Path]:
        paths: list[Path] = []
        for item in history:
            if item.get("tool") not in {"edit_code", "apply_test_patch"}:
                continue
            result = item.get("result")
            if not isinstance(result, dict) or not result.get("success"):
                continue
            raw_paths = list(result.get("artifacts") or [])
            data_path = result.get("data", {}).get("path") if isinstance(result.get("data"), dict) else None
            if data_path:
                raw_paths.append(data_path)
            for raw_path in raw_paths:
                try:
                    path = Path(str(raw_path)).resolve()
                    path.relative_to(project_root)
                except (OSError, ValueError):
                    continue
                if path.is_file() and path not in paths:
                    paths.append(path)
        return paths

    def _resolve_gitee_repo(self, project_root: Path) -> tuple[str, str]:
        owner = self.config.gitee.owner.strip()
        repo = self.config.gitee.repo.strip()
        if owner and repo and owner != "your-owner" and repo != "your-repo":
            return owner, repo

        remote = self._run_git(project_root, ["git", "remote", "get-url", "origin"])
        if remote.returncode != 0:
            return owner, repo
        parsed_owner, parsed_repo = self._parse_gitee_remote(remote.stdout.strip())
        return owner if owner and owner != "your-owner" else parsed_owner, repo if repo and repo != "your-repo" else parsed_repo

    def _parse_gitee_remote(self, remote_url: str) -> tuple[str, str]:
        if remote_url.startswith("git@"):
            match = re.search(r"gitee\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$", remote_url)
            return (match.group("owner"), match.group("repo")) if match else ("", "")
        parsed = urlparse(remote_url)
        parts = [part for part in parsed.path.strip("/").split("/") if part]
        if len(parts) >= 2:
            repo = parts[1][:-4] if parts[1].endswith(".git") else parts[1]
            return parts[0], repo
        return "", ""

    def _create_gitee_pull_request(self, owner: str, repo: str, branch: str, base_branch: str, bug_event: BugEvent) -> ToolResult:
        url = f"{self.config.gitee.base_url.rstrip('/')}/repos/{owner}/{repo}/pulls"
        payload = {
            "access_token": self.config.gitee.token.strip(),
            "title": f"[auto-fix] {bug_event.exception_type or bug_event.bug_id}",
            "head": branch,
            "base": base_branch,
            "body": (
                f"Auto repair for `{bug_event.bug_id}`.\n\n"
                f"Exception: `{bug_event.exception_type}`\n\n"
                f"Top frame: `{bug_event.top_business_frame}`"
            ),
        }
        try:
            response = requests.post(url, data=payload, timeout=30)
        except requests.RequestException as exc:
            return self._pr_error(f"gitee request failed: {exc}", branch=branch, owner=owner, repo=repo)
        if response.status_code >= 400:
            return self._pr_error(f"gitee pr create failed: HTTP {response.status_code} {response.text[:500]}", branch=branch, owner=owner, repo=repo)
        data = response.json() if response.content else {}
        pr_url = str(data.get("html_url") or data.get("url") or data.get("number") or "")
        if not pr_url:
            return self._pr_error("gitee response missing pr url", branch=branch, owner=owner, repo=repo)
        return ToolResult(tool="create_pr", success=True, exit_code=0, stdout_summary=pr_url, stderr_summary="", data={"pr_url": pr_url, "branch": branch}, artifacts=[])

    def _repair_branch_name(self, bug_id: str) -> str:
        safe_bug_id = re.sub(r"[^A-Za-z0-9._-]+", "-", bug_id).strip("-").lower() or "bug"
        return f"agent-fix/{safe_bug_id}"

    def _pr_error(self, message: str, **data: Any) -> ToolResult:
        return ToolResult(tool="create_pr", success=False, exit_code=1, stdout_summary=str(data.pop("stdout", "")), stderr_summary=message, data=data, artifacts=[])

    def _send_feishu_review_request(self, task: RepairTask, bug_event: BugEvent, session: dict[str, Any], pr_result: ToolResult, compile_result: ToolResult, test_result: ToolResult) -> ToolResult:
        """Notify Feishu that a validated PR is waiting for human review."""
        if not self.config.agent.review_required:
            result = ToolResult(tool="feishu_tool", success=True, exit_code=0, stdout_summary="review notification skipped", stderr_summary="", data={"skipped": True}, artifacts=[])
            session["feishu_review_result"] = result.model_dump()
            self._save_session(bug_event.bug_id, session)
            return result
        pr_url = str(pr_result.data.get("pr_url") or pr_result.stdout_summary)
        payload = {
            "action": "send_review_request_card",
            "args": {
                "bug": bug_event.model_dump(mode="json"),
                "bug_id": bug_event.bug_id,
                "task_id": task.id,
                "pr_url": pr_url,
                "agent_branch": str(pr_result.data.get("branch") or task.agent_branch),
                "base_branch": str(pr_result.data.get("base_branch") or task.base_branch),
                "compile_result": compile_result.model_dump(),
                "test_result": test_result.model_dump(),
                "create_pr_result": pr_result.model_dump(),
                **self._review_callback_urls(bug_event.bug_id),
            },
        }
        result = self._run_feishu_tool(payload)
        session["feishu_review_payload"] = payload
        session["feishu_review_result"] = result.model_dump()
        self._save_session(bug_event.bug_id, session)
        if result.success:
            ui.info(f"Feishu review notification sent  dry_run={result.data.get('dry_run')}")
        else:
            ui.warning(f"Feishu review notification failed  dry_run={result.data.get('dry_run')}")
        return result

    def _send_feishu_help(self, bug_event: BugEvent, session: dict[str, Any], last_result: ToolResult | None) -> ToolResult:
        """失败后记录飞书求助信息。"""
        payload = {
            "action": "send_help_card",
            "args": {
                "bug": bug_event.model_dump(mode="json"),
                "last_result": last_result.model_dump() if last_result else {},
                "session_path": bug_event.bug_id,
            },
        }
        result = self._run_feishu_tool(payload)
        record = {"bug": bug_event.model_dump(mode="json"), "last_result": last_result.model_dump() if last_result else None, "feishu_result": result.model_dump()}
        self.session_store.put(f"feishu_help:{bug_event.bug_id}", record)
        session["feishu_help_payload"] = payload
        session["feishu_help_result"] = result.model_dump()
        self._save_session(bug_event.bug_id, session)
        if result.success:
            ui.info(f"Feishu help notification sent  dry_run={result.data.get('dry_run')}")
        else:
            ui.warning(f"Feishu help notification failed  dry_run={result.data.get('dry_run')}")
        return result

    def _notify_whitelist_denial(self, bug_event: BugEvent, session: dict[str, Any], denied_entry: dict[str, Any]) -> ToolResult:
        """发送白名单拒绝通知，方便人工后续加入白名单。"""
        safe_denied_entry = {k: v for k, v in denied_entry.items() if k != "action"}
        payload = {
            "action": "send_help_card",
            "args": {
                "bug": bug_event.model_dump(mode="json"),
                "last_result": {
                    "tool": denied_entry.get("tool", ""),
                    "success": False,
                    "exit_code": 1,
                    "stdout_summary": "",
                    "stderr_summary": f"whitelist denied: {denied_entry.get('reason', '')}",
                    "data": safe_denied_entry,
                },
                "session_path": bug_event.bug_id,
            },
        }
        result = self._run_feishu_tool(payload)
        record = {"bug": bug_event.model_dump(mode="json"), "denied_entry": safe_denied_entry, "feishu_result": result.model_dump()}
        self.session_store.put(f"whitelist_denied:{bug_event.bug_id}", record)
        notifications = session.get("denial_notifications", [])
        if not isinstance(notifications, list):
            notifications = []
        notifications.append(record)
        session["denial_notifications"] = notifications
        session["last_denial_result"] = result.model_dump()
        self._save_session(bug_event.bug_id, session)
        ui.info(f"Whitelist denial notified  success={result.success}")
        return result

    def _run_feishu_tool(self, payload: dict[str, Any]) -> ToolResult:
        tool = self.registry.get("feishu_tool") or FeishuTool(self.config)
        try:
            return tool.run(payload)
        except Exception as exc:
            return ToolResult(tool="feishu_tool", success=False, exit_code=1, stdout_summary="", stderr_summary=str(exc), data={"payload": payload}, artifacts=[])

    def _review_callback_urls(self, bug_id: str) -> dict[str, str]:
        if self.config.feishu.review_callback_mode != "local":
            return {}
        base_url = self.config.feishu.review_callback_base_url.rstrip("/")
        if not base_url:
            return {}
        passed = urlencode({"event_type": "review_passed", "bug_id": bug_id})
        failed = urlencode({"event_type": "review_failed", "bug_id": bug_id})
        return {"review_pass_url": f"{base_url}/review?{passed}", "review_fail_url": f"{base_url}/review?{failed}"}
