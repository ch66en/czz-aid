from __future__ import annotations

"""定义从原始日志到修复入口的正式聚合流水线。"""

from dataclasses import dataclass, field
from typing import Any

from agent.code_nav.ast_symbols import JavaAstSymbolExtractor
from agent.core.dedup_engine import DedupEngine
from agent.core.repair_agent import RepairAgent, RepairRunResult
from agent.ingestion.sanitizer import Sanitizer
from agent.ingestion.traceback_parser import ParsedTraceback, TracebackParser
from agent.models import BugEvent
from agent.storage.session_store import SessionStore


@dataclass(slots=True)
class PipelineResult:
    """表示一次入口聚合处理的结果。"""

    bug_event: BugEvent
    parsed_traceback: ParsedTraceback
    sanitized_traceback: str
    session_snapshot: dict[str, Any] = field(default_factory=dict)
    repair_result: RepairRunResult | None = None


class IngestionPipeline:
    """把原始日志、脱敏、traceback 解析与修复运行串起来。"""

    def __init__(self, session_store: SessionStore, dedup_engine: DedupEngine | None = None, sanitizer: Sanitizer | None = None, traceback_parser: TracebackParser | None = None, repair_agent: RepairAgent | None = None) -> None:
        """初始化聚合流水线。"""
        self.session_store = session_store
        self.dedup_engine = dedup_engine or DedupEngine()
        self.sanitizer = sanitizer or Sanitizer()
        self.traceback_parser = traceback_parser or TracebackParser()
        self.repair_agent = repair_agent
        self.ast_extractor = JavaAstSymbolExtractor()

    def process(self, raw_text: str, bug_id: str, source: str, project: str, title: str = "", request_path: str = "", request_method: str = "", package_prefix: str | None = None) -> PipelineResult:
        """将原始日志处理成 BugEvent 并写入会话，必要时触发修复。"""
        sanitized_text = self.sanitizer.sanitize(raw_text)
        parsed = self.traceback_parser.parse(sanitized_text, package_prefix=package_prefix)
        bug_event = BugEvent(
            bug_id=bug_id,
            source=source,
            project=project,
            title=title,
            exception_type=parsed.exception_type,
            message=parsed.message,
            traceback=parsed.normalized_trace,
            frames=parsed.frames,
            request_path=request_path,
            request_method=request_method,
            top_business_frame=parsed.top_business_frame,
            fingerprint=self.dedup_engine.build_fingerprint(
                BugEvent(
                    bug_id=bug_id,
                    source=source,
                    project=project,
                    title=title,
                    exception_type=parsed.exception_type,
                    message=parsed.message,
                    traceback=parsed.normalized_trace,
                    frames=parsed.frames,
                    request_path=request_path,
                    request_method=request_method,
                    top_business_frame=parsed.top_business_frame,
                    fingerprint="",
                )
            ),
        )
        frame_contexts = self._collect_frame_contexts(parsed.frames)
        bug_event_dict = bug_event.model_dump()
        bug_event_dict["frame_contexts"] = frame_contexts
        bug_event = BugEvent.model_validate(bug_event_dict)
        duplicate = self.dedup_engine.is_duplicate(bug_event.fingerprint)
        if not duplicate:
            self._save_bug_event(bug_event, frame_contexts)
            self.dedup_engine.mark_seen(bug_event.fingerprint)
        session_snapshot = self._load_session_snapshot(bug_id)
        if frame_contexts:
            session_snapshot = {**session_snapshot, "frame_contexts": frame_contexts}
        repair_result = None
        if self.repair_agent is not None and not duplicate:
            repair_result = self.repair_agent.repair(bug_id)
        return PipelineResult(
            bug_event=bug_event,
            parsed_traceback=parsed,
            sanitized_traceback=parsed.normalized_trace,
            session_snapshot=session_snapshot,
            repair_result=repair_result,
        )

    def _collect_frame_contexts(self, frames: list[Any]) -> list[dict[str, Any]]:
        contexts: list[dict[str, Any]] = []
        for frame in frames:
            if not frame.file_path.endswith(".java") or frame.line_number <= 0:
                continue
            try:
                path = self._resolve_source_path(frame.file_path)
                if not path.exists():
                    continue
                symbol_at = self.ast_extractor.find_symbol_at(str(path), frame.line_number)
                symbol = symbol_at["symbol"]
                code = symbol_at["code"]
                contexts.append(
                    {
                        "filePath": str(path),
                        "symbolId": symbol["symbolId"],
                        "symbol": symbol,
                        "startLine": symbol["startLine"],
                        "endLine": symbol["endLine"],
                        "code": code,
                        "contentHash": symbol_at["contentHash"],
                    }
                )
            except Exception:
                continue
        return contexts

    def _resolve_source_path(self, file_path: str):
        from pathlib import Path

        path = Path(file_path)
        if path.exists():
            return path
        candidate = Path(self.session_store.root_dir) / file_path if hasattr(self.session_store, "root_dir") else path
        return candidate

    def _save_bug_event(self, bug_event: BugEvent, frame_contexts: list[dict[str, Any]] | None = None) -> None:
        """将 BugEvent 写入会话存储，供修复阶段读取。"""
        payload = bug_event.model_dump()
        if frame_contexts is not None:
            payload["frame_contexts"] = frame_contexts
        self.session_store.put(f"bug_event:{bug_event.bug_id}", payload)

    def _load_session_snapshot(self, bug_id: str) -> dict[str, Any]:
        """读取当前修复会话快照。"""
        session = self.session_store.get(bug_id)
        return session if isinstance(session, dict) else {}
