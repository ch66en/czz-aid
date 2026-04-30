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
from agent.core.permission_guard import PermissionGuard
from agent.core.task_manager import TaskManager
from agent.core.tool_registry import ToolRegistry
from agent.ingestion.sanitizer import Sanitizer
from agent.llm.openai_compatible_client import OpenAICompatibleClient
from agent.models import BugEvent, RepairTask, TaskStatus, ToolResult, ToolSpec
from agent.storage.session_store import SessionStore
from agent.storage.skill_store import SkillStore
from agent.tools.base import ToolContext
from agent.tools.ast_symbols_tool import AstSymbolsTool
from agent.tools.compile_tool import RunCompileTool
from agent.tools.edit_code import EditCodeTool
from agent.tools.feishu_tool import FeishuTool
from agent.tools.git_diff import GitDiffTool
from agent.tools.read_code import ReadCodeTool
from agent.tools.read_symbol_at_tool import ReadSymbolAtTool
from agent.tools.run_command import RunCommandTool
from agent.tools.search_code import SearchCodeTool
from agent.tools.test_tool import RunTestTool


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
    ) -> None:
        """初始化修复代理所依赖的各项组件。"""
        self.config = config
        self.registry = registry
        self.permission_guard = permission_guard
        self.task_manager = task_manager
        self.session_store = session_store
        self.skill_store = skill_store
        self.llm_client = llm_client
        self.sanitizer = Sanitizer()
        self._ensure_core_tools()

    def _ensure_core_tools(self) -> None:
        """确保核心工具已注册到工具注册表。"""
        for tool in [
            AstSymbolsTool(),
            ReadSymbolAtTool(),
            ReadCodeTool(self.config),
            SearchCodeTool(),
            EditCodeTool(self.config),
            RunCommandTool(self.config),
            GitDiffTool(),
            RunCompileTool(self.config),
            RunTestTool(self.config),
        ]:
            if self.registry.get(tool.spec.name) is None:
                self.registry.register(tool)

    def build_prompt_template(self, bug_event: BugEvent, skills: list[str], session: dict[str, Any]) -> str:
        """构造修复代理系统提示词模板。"""
        frame_contexts = session.get("frame_contexts", []) if isinstance(session, dict) else []
        prompt_session = dict(session)
        prompt_session.pop("frame_contexts", None)
        bug_summary = bug_event.model_dump(exclude={"traceback"})
        bug_summary["traceback_omitted"] = bool(bug_event.traceback)
        prompt = {
            "role": "You are an automated Java repair agent. Use native function tools to inspect and repair Java bugs.",
            "rules": [
                "Use only the available function tools; do not describe tool calls in plain text.",
                "Call exactly one function tool per turn.",
                "Use bug_event.frames and frame_contexts to locate the failure; full traceback is omitted to avoid repeated stack noise.",
                "Prefer the top business frame as the first repair target unless evidence points elsewhere.",
                "Use search_code for project-scoped Java keyword searches; its root is enforced by runtime.",
                "edit_code content must be a unified diff/patch, not a full file rewrite.",
                "After a successful edit, finish_patch triggers compile and test; do not skip them.",
                "Return finish_patch only after a code edit has succeeded.",
            ],
            "terminal_tools": {
                "finish_patch": "Call after a successful edit_code when the patch is ready for compile/test.",
            },
            "project": {"name": bug_event.project, "root": self.config.project.root},
            "bug_event": bug_summary,
            "frame_contexts": frame_contexts,
            "skills": skills,
            "session": prompt_session,
        }
        return json.dumps(prompt, ensure_ascii=False, default=str)
        tool_usage_notes = [
            "1. 只能从 Tools 列表中选择工具名，不允许臆造新工具。",
            "2. 遇到 Java traceback 或测试失败中的 file:line 时，必须优先使用 read_symbol_at(path, line) 或 ast_symbols(path) 定位。",
            "3. 不能在有 file:line 的情况下直接读取整个大文件。",
            "4. read_symbol_at 返回的 path、symbolId、startLine、endLine、code、contentHash 是后续分析和补丁定位依据。",
            "5. 如果 read_code 不传 start_line/end_line 且文件太大失败，应改用 ast_symbols/read_symbol_at。",
            "6. 修改代码前必须至少读取相关函数代码。",
            "7. edit_code 只能在写入允许目录后再执行，content 必须使用统一 diff/patch 格式，且修改后必须走 compile -> test。",
            "8. run_compile 是唯一允许的编译步骤，禁止直接跳过。",
            "9. run_test 是唯一允许的测试步骤，编译成功后必须执行。",
            "10. run_command 只能用于白名单命令，不允许危险命令。",
            "11. finish_patch 只能在已经完成代码修改后输出，表示进入 compile/test 阶段。",
            "12. 若 compile/test 失败，必须把失败摘要纳入下一轮决策。",
        ]
        return (
            "你是一个受严格流程约束的自动修复代理。\n"
            "必须遵循以下硬约束：\n"
            "1. 先检索相关技能，再做工具调用。\n"
            "2. 如果 FrameContexts 非空，必须先读取并理解相关函数体，再决定修改。\n"
            "3. 当 traceback 对应的业务函数已经通过 FrameContexts / read_symbol_at / read_code 完整暴露，并且错误点可直接从源码中判断时，应停止继续收集信息，直接输出修复方案并调用 edit_code。\n"
            "4. edit_code 的 content 必须使用统一 diff/patch 格式，禁止输出整文件全文。\n"
            "5. patch 应尽量只覆盖 traceback 命中的函数或最小相关代码块；通常只包含一个 hunk。\n"
            "6. patch 示例：--- a/src/main/java/X.java\\n+++ b/src/main/java/X.java\\n@@\\n-        badLine();\\n+        fixedLine();\n"
            "6. 只有在当前上下文不足以定位根因时，才继续使用 read_symbol_at(path, line) 或 ast_symbols(path) 深入定位。\n"
            "7. 不能在有 file:line 的情况下直接读取整个大文件。\n"
            "8. read_symbol_at 返回的 path、symbolId、startLine、endLine、code、contentHash 是后续分析和补丁定位依据。\n"
            "9. 修改代码前必须至少读取相关函数代码。\n"
            "10. 允许多步工具调用，但最多自动修复 3 轮。\n"
            "11. 任何修改后都必须执行编译。\n"
            "12. 编译成功后必须执行测试。\n"
            "13. 禁止跳过 compile。\n"
            "14. 禁止跳过 test。\n"
            "15. 禁止自动合并 PR。\n"
            "16. 成功后进入创建 PR 流程，失败后发送飞书求助。\n"
            "4. 只有在当前上下文不足以定位根因时，才继续使用 read_symbol_at(path, line) 或 ast_symbols(path) 深入定位。\n"
            "5. 只有在当前上下文不足以定位根因时，才继续使用 read_symbol_at(path, line) 或 ast_symbols(path) 深入定位。\n"
            "6. 不能在有 file:line 的情况下直接读取整个大文件。\n"
            "7. read_symbol_at 返回的 path、symbolId、startLine、endLine、code、contentHash 是后续分析和补丁定位依据。\n"
            "8. 修改代码前必须至少读取相关函数代码。\n"
            "9. 允许多步工具调用，但最多自动修复 3 轮。\n"
            "10. 任何修改后都必须执行编译。\n"
            "11. 编译成功后必须执行测试。\n"
            "12. 禁止跳过 compile。\n"
            "13. 禁止跳过 test。\n"
            "14. 禁止自动合并 PR。\n"
            "15. 成功后进入创建 PR 流程，失败后发送飞书求助。\n"
            "\n"
            f"BugEvent: {bug_event.model_dump_json()}\n"
            f"Skills: {json.dumps(skills, ensure_ascii=False)}\n"
            f"Session: {json.dumps(session, ensure_ascii=False)}\n"
            f"FrameContexts: {json.dumps(session.get('frame_contexts', []), ensure_ascii=False)}\n"
            f"Tools: {json.dumps(tool_specs, ensure_ascii=False)}\n"
            f"ToolUsageNotes: {json.dumps(tool_usage_notes, ensure_ascii=False)}\n"
            "\n"
            "LLM 必须只输出 JSON action，格式如下：\n"
            '{"tool":"SearchCode","arguments":{},"reason":"..."}\n'
            '当准备结束修改时输出 {"tool":"finish_patch","arguments":{},"reason":"..."}。\n'
            "如果编译或测试失败，把失败摘要作为上下文继续下一轮。"
        )

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
        skills = self._load_skills(bug_event.project)
        prompt_template = self.build_prompt_template(bug_event, skills, session)
        history: list[dict[str, Any]] = []
        llm_messages: list[dict[str, Any]] = [{"role": "system", "content": prompt_template}]
        last_result: ToolResult | None = None

        print(f"[repair] start bug_id={bug_id} exception={bug_event.exception_type}", flush=True)
        for attempt in range(1, self.config.agent.max_retry + 1):
            print(f"[repair] attempt={attempt} bug_id={bug_id}", flush=True)
            modified = False
            invalid_output_retries = 0
            finish_without_patch_rejections = 0
            while True:
                action = self._ask_llm(llm_messages, history, bug_event, session)
                history.append(action)
                tool_name = str(action.get("tool", ""))
                print(f"[repair] action tool={tool_name} reason={action.get('reason', '')}", flush=True)
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
                    self._append_tool_result_message(llm_messages, action, last_result)
                    invalid_output_retries += 1
                    if invalid_output_retries <= 2:
                        print("[repair] invalid llm output rejected; requesting native tool_call again", flush=True)
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
                        self._append_tool_result_message(llm_messages, action, last_result)
                        finish_without_patch_rejections += 1
                        if finish_without_patch_rejections <= 2:
                            print("[repair] finish_patch rejected reason=no successful edit_code", flush=True)
                            continue
                        break
                    compile_result = self._run_compile()
                    history.append({"tool": "RunCompileTool", "result": compile_result.model_dump()})
                    session["compile_result"] = compile_result.model_dump()
                    self._save_session(bug_id, session)
                    print(f"[repair] compile success={compile_result.success} exit_code={compile_result.exit_code}", flush=True)
                    if not compile_result.success:
                        last_result = compile_result
                        break
                    test_result = self._run_test()
                    history.append({"tool": "RunTestTool", "result": test_result.model_dump()})
                    session["test_result"] = test_result.model_dump()
                    self._save_session(bug_id, session)
                    print(f"[repair] test success={test_result.success} exit_code={test_result.exit_code}", flush=True)
                    if not test_result.success:
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
                        print(f"[repair] create_pr failed bug_id={bug_id} error={pr_result.stderr_summary}", flush=True)
                        return RepairRunResult(False, "failed", "create pr failed", task=task, last_result=pr_result, prompt_template=prompt_template, history=history)
                    pr_url = str(pr_result.data.get("pr_url") or pr_result.stdout_summary)
                    task.pr_url = pr_url
                    session = {**session, "pr_url": pr_url, "status": "passed"}
                    review_result = self._send_feishu_review_request(task, bug_event, session, pr_result, compile_result, test_result)
                    if self.config.agent.review_required:
                        task.status = TaskStatus.REVIEWING
                        self.task_manager.update_status(bug_id, TaskStatus.REVIEWING)
                        session["status"] = "reviewing"
                        self._save_session(bug_id, session)
                        print(f"[repair] review requested bug_id={bug_id} pr_url={pr_url} feishu_success={review_result.success}", flush=True)
                        return RepairRunResult(True, "reviewing", "repair succeeded; review requested", task=task, last_result=test_result, prompt_template=prompt_template, history=history)
                    task.status = TaskStatus.PASSED
                    self.task_manager.update_status(bug_id, TaskStatus.PASSED)
                    self._save_session(bug_id, session)
                    print(f"[repair] passed bug_id={bug_id} pr_url={pr_url}", flush=True)
                    return RepairRunResult(True, "passed", "repair succeeded", task=task, last_result=test_result, prompt_template=prompt_template, history=history)

                tool = self.registry.get(tool_name.lower()) or self.registry.get(tool_name)
                if tool is None:
                    last_result = ToolResult(tool=tool_name or "unknown", success=False, exit_code=1, stdout_summary="", stderr_summary=f"tool not found: {tool_name}", data={}, artifacts=[])
                    history.append({"tool": tool_name or "unknown", "result": last_result.model_dump()})
                    session["last_error"] = last_result.model_dump()
                    self._append_session_tool_call(session, action, last_result)
                    self._save_session(bug_id, session)
                    self._append_tool_result_message(llm_messages, action, last_result)
                    continue

                arguments = self._prepare_tool_arguments(tool.spec.name, action.get("arguments", {}), bug_event)
                action["arguments"] = arguments
                allowed, reason = self.permission_guard.can_execute(tool.spec, ToolContext(permission_mode={tool.permission}), arguments)
                if not allowed:
                    last_result = ToolResult(tool=tool.spec.name, success=False, exit_code=1, stdout_summary="", stderr_summary=reason, data={}, artifacts=[])
                    history.append({"tool": tool.spec.name, "result": last_result.model_dump()})
                    session["last_error"] = last_result.model_dump()
                    self._append_session_tool_call(session, action, last_result)
                    self._save_session(bug_id, session)
                    print(f"[repair] denied tool={tool.spec.name} reason={reason}", flush=True)
                    self._append_tool_result_message(llm_messages, action, last_result)
                    continue

                result = tool.run(arguments)
                history.append({"tool": tool.spec.name, "result": result.model_dump()})
                last_result = result
                session["last_tool_result"] = result.model_dump()
                self._append_session_tool_call(session, action, result)
                self._save_session(bug_id, session)
                self._append_tool_result_message(llm_messages, action, result)
                print(f"[repair] tool={tool.spec.name} success={result.success} exit_code={result.exit_code}", flush=True)
                if tool.spec.name == "edit_code" and result.success and self._is_valid_patch(result, session):
                    modified = True
                elif tool.spec.name == "edit_code" and result.success:
                    session["last_error"] = {"tool": "edit_code", "error": "invalid patch target"}
                    self._save_session(bug_id, session)
                    print("[repair] invalid patch target", flush=True)

                # 继续在当前轮次中等待 finish_patch，直到进入编译/测试阶段。
                continue

            # 当前轮次结束，但测试失败后才算一轮失败，进入下一轮。
            continue

        self.task_manager.update_status(bug_id, TaskStatus.FAILED)
        self._send_feishu_help(bug_event, session, last_result)
        print(f"[repair] failed bug_id={bug_id} message=auto repair exhausted", flush=True)
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

    def _load_skills(self, project: str) -> list[str]:
        """按项目检索相关技能。"""
        skills: list[str] = []
        for key in list(getattr(self.skill_store, "_items", {}).keys()):
            if project in key or not skills:
                value = self.skill_store.get(key)
                if value:
                    skills.append(value)
        return skills

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
            print(f"[repair] llm_call saved={artifact_path}", flush=True)
        if not response.success:
            return self._invalid_llm_action(f"llm call failed: {response.stderr_summary}")
        tool_calls = response.data.get("tool_calls") if isinstance(response.data, dict) else None
        content = response.data.get("content") if isinstance(response.data, dict) else ""
        if isinstance(tool_calls, list) and tool_calls:
            tool_call = tool_calls[0]
            messages.append(self._assistant_tool_call_message(tool_call, str(content or "")))
            return self._tool_call_to_action(tool_call)
        payload = content
        if isinstance(payload, str):
            messages.append({"role": "assistant", "content": payload})
            parsed_payload = self._parse_llm_action_payload(payload)
            if parsed_payload is None:
                return self._invalid_llm_action("invalid llm output: expected a native function tool_call")
            return self._normalize_llm_action(parsed_payload)
        return self._invalid_llm_action("no llm output")

    def _openai_tools(self) -> list[dict[str, Any]]:
        """Convert registered tools plus virtual terminal actions to OpenAI function tools."""
        specs = [tool.spec for tool in self.registry.list_tools()]
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

    def _append_tool_result_message(self, messages: list[dict[str, Any]], action: dict[str, Any], result: ToolResult) -> None:
        call_id = str(action.get("tool_call_id") or "")
        content = self._tool_result_content(result)
        if call_id:
            messages.append({"role": "tool", "tool_call_id": call_id, "content": content})
            return
        messages.append({"role": "user", "content": content})

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

    def _is_valid_patch(self, result: ToolResult, session: dict[str, Any]) -> bool:
        """判断 edit_code 是否修改了真实业务源码。"""
        path = str((result.data or {}).get("path", ""))
        if not path:
            return False
        normalized = path.replace("\\", "/").lower()
        if "/tmp/" in f"/{normalized}/" or normalized.startswith("tmp/") or normalized.startswith("./tmp/"):
            return False
        if not normalized.endswith(".java"):
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
            ["git", "add", *[str(path.relative_to(project_root)) for path in edited_paths]],
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
        return subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, shell=False, check=False)

    def _edited_paths_from_history(self, history: list[dict[str, Any]], project_root: Path) -> list[Path]:
        paths: list[Path] = []
        for item in history:
            if item.get("tool") != "edit_code":
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
        print(f"[repair] feishu review success={result.success} dry_run={result.data.get('dry_run')}", flush=True)
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
        record = {"bug": bug_event.model_dump(mode="json"), "session": session, "last_result": last_result.model_dump() if last_result else None, "feishu_result": result.model_dump()}
        self.session_store.put(f"feishu_help:{bug_event.bug_id}", record)
        session["feishu_help_payload"] = payload
        session["feishu_help_result"] = result.model_dump()
        self._save_session(bug_event.bug_id, session)
        print(f"[repair] feishu help success={result.success} dry_run={result.data.get('dry_run')}", flush=True)
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
