from __future__ import annotations

"""Resolve bounded repair-time context from BugEvent and session data."""

from pathlib import Path
import re
from typing import Any

from agent.models import BugEvent, StackFrame


class RepairContextResolver:
    """Normalize Java symbols without treating class packages as modules."""

    def __init__(self, module_aliases: dict[str, str] | None = None) -> None:
        self.module_aliases = module_aliases or {}

    def resolve(self, bug_event: BugEvent, session: dict[str, Any] | None = None) -> dict[str, Any]:
        frame = self._business_frame(bug_event.frames)
        class_name, package_name = self._class_and_package(frame)
        method_name = frame.function_name if frame is not None else ""
        modules = self._module_candidates(frame, package_name, session or {})
        symbols = self._session_symbols(session or {})
        return {
            "project": bug_event.project,
            "module_candidates": modules,
            "exception_type": bug_event.exception_type,
            "message": bug_event.message,
            "class_name": class_name,
            "method_name": method_name,
            "package_name": package_name,
            "top_business_frame": bug_event.top_business_frame,
            "request_path": bug_event.request_path,
            "repair_stage": "before_edit",
            "root_cause_hint": self._root_cause_hint(bug_event),
            "symbols": symbols,
        }

    def _business_frame(self, frames: list[StackFrame]) -> StackFrame | None:
        return next((frame for frame in frames if frame.is_business_code), None) or (frames[0] if frames else None)

    def _class_and_package(self, frame: StackFrame | None) -> tuple[str, str]:
        if frame is None:
            return "", ""
        qualified = frame.module_name.strip()
        if "." in qualified:
            package, _, class_name = qualified.rpartition(".")
            return class_name, package
        return Path(frame.file_path).stem, ""

    def _module_candidates(self, frame: StackFrame | None, package_name: str, session: dict[str, Any]) -> list[str]:
        candidates: list[str] = []
        qualified = f"{package_name}.{Path(frame.file_path).stem}".strip(".") if frame is not None else package_name
        for prefix, module in self.module_aliases.items():
            if qualified.startswith(prefix) or package_name.startswith(prefix):
                candidates.append(module)
        if frame is not None and frame.module_name and "." not in frame.module_name and frame.module_name.islower():
            candidates.append(frame.module_name)
        for context in session.get("frame_contexts", []) if isinstance(session.get("frame_contexts"), list) else []:
            if not isinstance(context, dict):
                continue
            module = str(context.get("module") or "").strip()
            if module:
                candidates.append(module)
            file_path = str(context.get("filePath") or "")
            match = re.search(r"(?:^|[/\\])modules?[/\\]([^/\\]+)", file_path, flags=re.IGNORECASE)
            if match:
                candidates.append(match.group(1))
        return list(dict.fromkeys(item for item in candidates if item))

    def _session_symbols(self, session: dict[str, Any]) -> list[str]:
        symbols: list[str] = []
        frame_contexts = session.get("frame_contexts", [])
        if not isinstance(frame_contexts, list):
            return symbols
        for context in frame_contexts:
            if not isinstance(context, dict):
                continue
            for key in ("symbol", "symbolId", "target"):
                value = str(context.get(key) or "").strip()
                if value and len(value) <= 160:
                    symbols.append(value)
        return list(dict.fromkeys(symbols))[:12]

    def _root_cause_hint(self, bug_event: BugEvent) -> str:
        text = f"{bug_event.exception_type} {bug_event.message}".lower()
        rules = [
            ("nullpointerexception", "possible null dereference"),
            ("nosuchelementexception", "missing optional or collection value"),
            ("arrayindexoutofboundsexception", "invalid array index boundary"),
            ("illegalargumentexception", "invalid input validation"),
        ]
        for token, hint in rules:
            if token in text:
                return hint
        return ""
