from __future__ import annotations

"""实现带粗流程约束的修复代理运行时。"""

import json
from dataclasses import dataclass, field
from typing import Any

from agent.config import AppConfig
from agent.core.permission_guard import PermissionGuard
from agent.core.task_manager import TaskManager
from agent.core.tool_registry import ToolRegistry
from agent.ingestion.sanitizer import Sanitizer
from agent.llm.openai_compatible_client import OpenAICompatibleClient
from agent.models import BugEvent, RepairTask, TaskStatus, ToolResult
from agent.storage.session_store import SessionStore
from agent.storage.skill_store import SkillStore
from agent.tools.base import ToolContext
from agent.tools.compile_tool import RunCompileTool
from agent.tools.edit_code import EditCodeTool
from agent.tools.git_diff import GitDiffTool
from agent.tools.read_code import ReadCodeTool
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
            ReadCodeTool(),
            SearchCodeTool(),
            EditCodeTool(),
            RunCommandTool(self.config),
            GitDiffTool(),
            RunCompileTool(self.config),
            RunTestTool(self.config),
        ]:
            if self.registry.get(tool.spec.name) is None:
                self.registry.register(tool)

    def build_prompt_template(self, bug_event: BugEvent, skills: list[str], session: dict[str, Any]) -> str:
        """构造修复代理系统提示词模板。"""
        tool_specs = [tool.spec.model_dump() if hasattr(tool.spec, "model_dump") else dict(vars(tool.spec)) for tool in self.registry.list_tools()]
        tool_usage_notes = [
            "1. 只能从 Tools 列表中选择工具名，不允许臆造新工具。",
            "2. edit_code 只能在写入允许目录后再执行，且修改后必须走 compile -> test。",
            "3. run_compile 是唯一允许的编译步骤，禁止直接跳过。",
            "4. run_test 是唯一允许的测试步骤，编译成功后必须执行。",
            "5. run_command 只能用于白名单命令，不允许危险命令。",
            "6. finish_patch 只能在已经完成代码修改后输出，表示进入 compile/test 阶段。",
            "7. 若 compile/test 失败，必须把失败摘要纳入下一轮决策。",
        ]
        return (
            "你是一个受严格流程约束的自动修复代理。\n"
            "必须遵循以下硬约束：\n"
            "1. 先检索相关技能，再做工具调用。\n"
            "2. 允许多步工具调用，但最多自动修复 3 轮。\n"
            "3. 任何修改后都必须执行编译。\n"
            "4. 编译成功后必须执行测试。\n"
            "5. 禁止跳过 compile。\n"
            "6. 禁止跳过 test。\n"
            "7. 禁止自动合并 PR。\n"
            "8. 成功后进入创建 PR 流程，失败后发送飞书求助。\n"
            "\n"
            f"BugEvent: {bug_event.model_dump_json()}\n"
            f"Skills: {json.dumps(skills, ensure_ascii=False)}\n"
            f"Session: {json.dumps(session, ensure_ascii=False)}\n"
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
        skills = self._load_skills(bug_event.project)
        prompt_template = self.build_prompt_template(bug_event, skills, session)
        history: list[dict[str, Any]] = []
        last_result: ToolResult | None = None

        for _ in range(3):
            modified = False
            while True:
                action = self._ask_llm(prompt_template, history)
                history.append(action)
                tool_name = str(action.get("tool", ""))
                if tool_name == "finish_patch":
                    if not modified:
                        last_result = ToolResult(tool="finish_patch", success=False, exit_code=1, stdout_summary="no patch produced", stderr_summary="", data={}, artifacts=[])
                        session["last_error"] = last_result.model_dump()
                        self._save_session(bug_id, session)
                        break
                    compile_result = self._run_compile()
                    history.append({"tool": "RunCompileTool", "result": compile_result.model_dump()})
                    session["compile_result"] = compile_result.model_dump()
                    self._save_session(bug_id, session)
                    if not compile_result.success:
                        last_result = compile_result
                        break
                    test_result = self._run_test()
                    history.append({"tool": "RunTestTool", "result": test_result.model_dump()})
                    session["test_result"] = test_result.model_dump()
                    self._save_session(bug_id, session)
                    if not test_result.success:
                        last_result = test_result
                        break
                    pr_url = self._create_pr(task, bug_event)
                    task.pr_url = pr_url
                    task.status = TaskStatus.PASSED
                    self.task_manager.update_status(bug_id, TaskStatus.PASSED)
                    self._save_session(bug_id, {**session, "pr_url": pr_url, "status": "passed"})
                    return RepairRunResult(True, "passed", "repair succeeded", task=task, last_result=test_result, prompt_template=prompt_template, history=history)

                tool = self.registry.get(tool_name.lower()) or self.registry.get(tool_name)
                if tool is None:
                    last_result = ToolResult(tool=tool_name or "unknown", success=False, exit_code=1, stdout_summary="", stderr_summary=f"tool not found: {tool_name}", data={}, artifacts=[])
                    session["last_error"] = last_result.model_dump()
                    self._save_session(bug_id, session)
                    continue

                allowed, reason = self.permission_guard.can_execute(tool.spec, ToolContext(permission_mode={tool.permission}), action.get("arguments", {}))
                if not allowed:
                    last_result = ToolResult(tool=tool.spec.name, success=False, exit_code=1, stdout_summary="", stderr_summary=reason, data={}, artifacts=[])
                    session["last_error"] = last_result.model_dump()
                    self._save_session(bug_id, session)
                    continue

                result = tool.run(action.get("arguments", {}))
                history.append({"tool": tool.spec.name, "result": result.model_dump()})
                last_result = result
                session["last_tool_result"] = result.model_dump()
                self._save_session(bug_id, session)
                if tool.spec.name == "edit_code" and result.success:
                    modified = True

                # 继续在当前轮次中等待 finish_patch，直到进入编译/测试阶段。
                continue

            # 当前轮次结束，但测试失败后才算一轮失败，进入下一轮。
            continue

        self.task_manager.update_status(bug_id, TaskStatus.FAILED)
        self._send_feishu_help(bug_event, session, last_result)
        return RepairRunResult(False, "failed", "auto repair exhausted", task=task, last_result=last_result, prompt_template=prompt_template, history=history)

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

    def _ask_llm(self, prompt_template: str, history: list[dict[str, Any]]) -> dict[str, Any]:
        """请求 LLM 输出下一步动作。"""
        if self.llm_client is None:
            if not any(item.get("tool") == "edit_code" for item in history):
                return {"tool": "edit_code", "arguments": {"path": "./tmp/fix.py", "content": "pass"}, "reason": "create patch"}
            return {"tool": "finish_patch", "arguments": {}, "reason": "done"}
        response = self.llm_client.chat([
            {"role": "system", "content": prompt_template},
            {"role": "user", "content": json.dumps(history, ensure_ascii=False)},
        ])
        payload = response.data.get("content") if isinstance(response.data, dict) else None
        if isinstance(payload, str):
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                return {"tool": "finish_patch", "arguments": {}, "reason": "invalid llm output"}
        return {"tool": "finish_patch", "arguments": {}, "reason": "no llm output"}

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

    def _create_pr(self, task: RepairTask, bug_event: BugEvent) -> str:
        """进入创建 PR 流程但不自动合并。"""
        pr_url = f"https://gitee.example/{bug_event.project}/pulls/{task.id}"
        self.session_store.put(f"pr:{task.id}", pr_url)
        return pr_url

    def _send_feishu_help(self, bug_event: BugEvent, session: dict[str, Any], last_result: ToolResult | None) -> None:
        """失败后记录飞书求助信息。"""
        self.session_store.put(f"feishu_help:{bug_event.bug_id}", {"bug": bug_event.model_dump(), "session": session, "last_result": last_result.model_dump() if last_result else None})
