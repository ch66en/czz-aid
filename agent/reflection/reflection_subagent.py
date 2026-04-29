from __future__ import annotations

"""实现反思子代理的硬编码流程。"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.config import AppConfig
from agent.core.dedup_engine import DedupEngine
from agent.ingestion.feishu_bug_receiver import FeishuBugReceiver
from agent.ingestion.sanitizer import Sanitizer
from agent.llm.openai_compatible_client import OpenAICompatibleClient
from agent.models import BugEvent, ReviewDecision, ReviewEvent
from agent.storage.session_store import SessionStore
from agent.storage.skill_store import SkillStore
from agent.tools.feishu_tool import FeishuTool
from agent.tools.git_tool import GitTool
from agent.reflection.diff_analyzer import DiffAnalyzer
from agent.reflection.skill_generator import SkillArtifact, SkillGenerator


@dataclass(slots=True)
class ReflectionResult:
    """表示一次反思流程的结果。"""

    success: bool
    message: str
    skill_artifact: SkillArtifact | None = None


class ReflectionSubAgent:
    """负责把审核结果沉淀为 skill 的硬编码流程。"""

    def __init__(
        self,
        config: AppConfig,
        session_store: SessionStore,
        skill_store: SkillStore,
        llm_client: OpenAICompatibleClient | None = None,
        feishu_tool: FeishuTool | None = None,
        git_tool: GitTool | None = None,
        dedup_engine: DedupEngine | None = None,
        diff_analyzer: DiffAnalyzer | None = None,
        skill_generator: SkillGenerator | None = None,
        sanitizer: Sanitizer | None = None,
    ) -> None:
        self.config = config
        self.session_store = session_store
        self.skill_store = skill_store
        self.llm_client = llm_client
        self.feishu_tool = feishu_tool or FeishuTool(config)
        self.git_tool = git_tool or GitTool()
        self.dedup_engine = dedup_engine or DedupEngine()
        self.diff_analyzer = diff_analyzer or DiffAnalyzer()
        self.skill_generator = skill_generator or SkillGenerator()
        self.sanitizer = sanitizer or Sanitizer()
        self.receiver = FeishuBugReceiver()

    def handle_review_event(self, payload: dict[str, Any]) -> ReflectionResult:
        review_event = self.receiver.receive(payload)
        if not isinstance(review_event, ReviewEvent):
            return ReflectionResult(False, "not a review event")
        if review_event.decision == ReviewDecision.REVIEW_PASSED.value:
            return self._handle_review_passed(review_event)
        if review_event.decision == ReviewDecision.REVIEW_FAILED.value:
            return self._handle_review_failed(review_event, payload)
        return ReflectionResult(False, f"unsupported decision: {review_event.decision}")

    def reflect(self, bug_id: str, result: str) -> str:
        review_event = ReviewEvent(task_id=bug_id, reviewer="system", decision=result, comment="legacy reflect")
        outcome = self._handle_review_passed(review_event) if result == "pass" else self._handle_review_failed(review_event, {"human_fix_branch": "legacy/human-fix"})
        return outcome.message

    def _handle_review_passed(self, review_event: ReviewEvent) -> ReflectionResult:
        session = self._load_session(review_event.task_id)
        bug_event = self._load_bug_event(review_event.task_id, session)
        prompt = self._build_summary_prompt(
            mode="review_passed",
            bug_event=bug_event,
            review_event=review_event,
            trace_full=self._load_artifact_text(session, "trace_full_sanitized.log"),
            trace_frames=self._load_artifact_json(session, "trace_frames.json"),
            tool_calls=self._load_artifact_text(session, "tool_calls.jsonl"),
            agent_patch=self._load_artifact_text(session, "agent_patch.diff"),
            compile_result=self._load_artifact_json(session, "compile_result.json"),
            test_result=self._load_artifact_json(session, "test_result.json"),
        )
        summary = self._ask_llm(prompt)
        skill_artifact = self._generate_and_persist_skill(bug_event=bug_event, review_event=review_event, title=bug_event.title or bug_event.exception_type, body=summary)
        self._update_session(review_event.task_id, {"reflection": "review_passed", "skill": skill_artifact.meta.model_dump()})
        self._notify_skill_created(skill_artifact, review_event.task_id)
        return ReflectionResult(True, "skill created from review_passed", skill_artifact=skill_artifact)

    def _handle_review_failed(self, review_event: ReviewEvent, payload: dict[str, Any]) -> ReflectionResult:
        human_fix_branch = str(payload.get("human_fix_branch", "")).strip()
        if not human_fix_branch:
            return ReflectionResult(False, "human_fix_branch is required for review_failed")
        session = self._load_session(review_event.task_id)
        bug_event = self._load_bug_event(review_event.task_id, session)
        base_branch = str(session.get("base_branch") or getattr(self.config.project, "base_branch", None) or self.config.project.default_branch)
        agent_branch = str(session.get("agent_branch", "")).strip()
        if not agent_branch:
            return ReflectionResult(False, "agent_branch is missing in session")
        agent_diff = self._git_diff(base_branch, agent_branch)
        human_diff = self._git_diff(base_branch, human_fix_branch)
        diff_summary = self.diff_analyzer.analyze(agent_diff, human_diff)
        prompt = self._build_summary_prompt(
            mode="review_failed",
            bug_event=bug_event,
            review_event=review_event,
            trace_full=self._load_artifact_text(session, "trace_full_sanitized.log"),
            trace_frames=self._load_artifact_json(session, "trace_frames.json"),
            tool_calls=self._load_artifact_text(session, "tool_calls.jsonl"),
            agent_patch=agent_diff,
            compile_result=self._load_artifact_json(session, "compile_result.json"),
            test_result=self._load_artifact_json(session, "test_result.json"),
            extra={"human_fix_branch": human_fix_branch, "human_diff": human_diff, "diff_summary": diff_summary.summary},
        )
        summary = self._ask_llm(prompt)
        skill_artifact = self._generate_and_persist_skill(bug_event=bug_event, review_event=review_event, title=bug_event.title or bug_event.exception_type, body=summary)
        self._update_session(review_event.task_id, {"reflection": "review_failed", "skill": skill_artifact.meta.model_dump(), "human_fix_branch": human_fix_branch, "agent_diff": agent_diff, "human_diff": human_diff})
        self._notify_skill_created(skill_artifact, review_event.task_id)
        return ReflectionResult(True, "skill created from review_failed", skill_artifact=skill_artifact)

    def _load_session(self, bug_id: str) -> dict[str, Any]:
        session = self.session_store.get(bug_id)
        return session if isinstance(session, dict) else {}

    def _load_bug_event(self, bug_id: str, session: dict[str, Any]) -> BugEvent:
        raw = self.session_store.get(f"bug_event:{bug_id}")
        if isinstance(raw, dict):
            return BugEvent.model_validate(raw)
        if isinstance(session.get("bug_event"), dict):
            return BugEvent.model_validate(session["bug_event"])
        fallback = BugEvent(bug_id=bug_id, source="unknown", project=self.config.project.name, title="", exception_type="UnknownError", message="", traceback="", fingerprint="")
        return BugEvent.model_validate({**fallback.model_dump(), "fingerprint": self.dedup_engine.build_fingerprint(fallback)})

    def _load_artifact_text(self, session: dict[str, Any], name: str) -> str:
        value = session.get(name)
        if isinstance(value, str):
            return value
        path = session.get("artifact_paths", {}).get(name) if isinstance(session.get("artifact_paths"), dict) else None
        if isinstance(path, str) and Path(path).exists():
            return Path(path).read_text(encoding="utf-8")
        return ""

    def _load_artifact_json(self, session: dict[str, Any], name: str) -> Any:
        text = self._load_artifact_text(session, name)
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def _build_summary_prompt(self, *, mode: str, bug_event: BugEvent, review_event: ReviewEvent, trace_full: str, trace_frames: Any, tool_calls: str, agent_patch: str, compile_result: Any, test_result: Any, extra: dict[str, Any] | None = None) -> str:
        payload = {
            "mode": mode,
            "bug_event": bug_event.model_dump(),
            "review_event": review_event.model_dump(),
            "trace_full": trace_full,
            "trace_frames": trace_frames,
            "tool_calls": tool_calls,
            "agent_patch": agent_patch,
            "compile_result": compile_result,
            "test_result": test_result,
            "extra": extra or {},
        }
        return (
            "你是一个只负责总结的反思模型，不负责选择工具。\n"
            "请基于输入内容输出结构化总结，覆盖适用场景、典型信号、有用步骤、多余步骤、推荐步骤、避免事项。\n"
            "如果是 review_failed，请重点比较 Agent 修复与人工修复的差异。\n"
            f"INPUT: {json.dumps(payload, ensure_ascii=False, default=str)}"
        )

    def _ask_llm(self, prompt: str) -> str:
        if self.llm_client is None:
            return "适用场景：通用。典型信号：异常栈。推荐步骤：先定位业务帧，再编译测试。避免事项：跳过验证。"
        response = self.llm_client.chat([{"role": "system", "content": prompt}])
        data = getattr(response, "data", {})
        if isinstance(data, dict):
            return str(data.get("content", ""))
        return str(getattr(response, "stdout_summary", ""))

    def _generate_and_persist_skill(self, *, bug_event: BugEvent, review_event: ReviewEvent, title: str, body: str) -> SkillArtifact:
        skill_name = self._build_skill_name(bug_event, review_event)
        skill_dir = Path(self.config.workspace) / "skills" / skill_name
        artifact = self.skill_generator.build(name=skill_name, description=title, source_bug_id=bug_event.bug_id, body=body, skill_dir=skill_dir)
        artifact.markdown_path.write_text(artifact.markdown, encoding="utf-8")
        artifact.meta_path.write_text(artifact.meta.model_dump_json(indent=2), encoding="utf-8")
        self.skill_store.put(skill_name, artifact.markdown)
        return artifact

    def _build_skill_name(self, bug_event: BugEvent, review_event: ReviewEvent) -> str:
        base = bug_event.title or bug_event.exception_type or bug_event.bug_id
        project_slug = self._safe_slug(bug_event.project, fallback="project", max_length=32)
        bug_slug = self._safe_slug(review_event.task_id or bug_event.bug_id, fallback="bug", max_length=32)
        title_slug = self._safe_slug(base, fallback=bug_event.bug_id, max_length=48)
        return f"skill-{project_slug}-{bug_slug}-{title_slug}"

    def _safe_slug(self, text: str, *, fallback: str, max_length: int) -> str:
        lowered = str(text or "").lower()
        slug = re.sub(r"[^a-z0-9._-]+", "-", lowered)
        slug = re.sub(r"-+", "-", slug).strip(" .-_")
        if not slug:
            slug = re.sub(r"[^a-z0-9._-]+", "-", fallback.lower()).strip(" .-_") or "item"
        reserved = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
        if slug in reserved:
            slug = f"{slug}-item"
        return slug[:max_length].rstrip(" .-_") or "item"

    def _git_diff(self, base_branch: str, target_branch: str) -> str:
        result = self.git_tool.run({"action": "diff", "args": {}})
        if result.success and result.stdout_summary:
            return result.stdout_summary
        return f"diff({base_branch}...{target_branch}) unavailable"

    def _notify_skill_created(self, artifact: SkillArtifact, bug_id: str) -> None:
        self.feishu_tool.run({"action": "send_skill_created_card", "args": {"bug_id": bug_id, "skill_name": artifact.meta.name, "skill_path": str(artifact.skill_dir)}})

    def _update_session(self, bug_id: str, patch: dict[str, Any]) -> None:
        session = self._load_session(bug_id)
        session.update(patch)
        self.session_store.put(bug_id, session)
