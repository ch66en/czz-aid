from __future__ import annotations

"""Isolated updater for per-bug summary.md files."""

import json
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from typing import Any

from agent.config import AppConfig
from agent.ingestion.sanitizer import Sanitizer
from agent.session_memory.models import MemoryUpdateEvent
from agent.session_memory.store import SUMMARY_TEMPLATE, SessionMemoryStore


SECTION_NAMES = [
    "Current State",
    "Bug",
    "Evidence",
    "Attempts",
    "Tool Results",
    "Files and Symbols",
    "Constraints",
    "Next Actions",
    "Final Outcome",
]


class SessionMemoryAgent:
    """Async, isolated session memory updater.

    The updater only reads and writes summary.md. It does not execute repair tools,
    mutate source files, or change the authoritative session state.
    """

    def __init__(self, config: AppConfig, store: SessionMemoryStore | None = None, sanitizer: Sanitizer | None = None) -> None:
        self.config = config
        self.store = store or SessionMemoryStore(config)
        self.sanitizer = sanitizer or Sanitizer()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="session-memory") if config.session_memory.async_enabled else None
        self._futures: list[Future[None]] = []

    def ensure(self, bug_id: str) -> str:
        return str(self.store.ensure_summary(bug_id))

    def enqueue(self, event: MemoryUpdateEvent) -> None:
        if not self.config.session_memory.enabled:
            return
        if self._executor is None:
            self.update(event)
            return
        future = self._executor.submit(self.update, event)
        self._futures.append(future)
        self._futures = [item for item in self._futures if not item.done()]

    def update(self, event: MemoryUpdateEvent) -> None:
        if not self.config.session_memory.enabled:
            return
        summary = self.store.read_summary(event.bug_id)
        sections = self._parse_sections(summary)
        self._merge_event(sections, event)
        content = self._render_sections(sections)
        sanitized = self.sanitizer.sanitize(content)
        max_chars = max(2000, self.config.session_memory.max_summary_chars)
        if len(sanitized) > max_chars:
            sanitized = self._trim_summary(sanitized, max_chars)
        self.store.write_summary(event.bug_id, sanitized)

    def wait_for_idle(self) -> None:
        for future in list(self._futures):
            future.result(timeout=10)
        self._futures.clear()

    def shutdown(self) -> None:
        self.wait_for_idle()
        if self._executor is not None:
            self._executor.shutdown(wait=True)

    def _parse_sections(self, text: str) -> dict[str, list[str]]:
        sections = {name: [] for name in SECTION_NAMES}
        current = ""
        for line in (text or SUMMARY_TEMPLATE).splitlines():
            if line.startswith("# "):
                name = line[2:].strip()
                current = name if name in sections else ""
                continue
            if current:
                sections[current].append(line)
        return sections

    def _merge_event(self, sections: dict[str, list[str]], event: MemoryUpdateEvent) -> None:
        timestamp = event.created_at.isoformat(timespec="seconds")
        result = event.result or {}
        success = result.get("success", "")
        exit_code = result.get("exit_code", "")
        stdout = str(result.get("stdout_summary", "") or "").strip()
        stderr = str(result.get("stderr_summary", "") or "").strip()
        status = event.session_status or {}
        tool = event.tool or str(result.get("tool") or "unknown")
        outcome = "success" if success else "failed"

        self._replace_section(
            sections,
            "Current State",
            [
                f"- Last event: `{tool}` {outcome} at {timestamp}.",
                f"- Last reason: {event.reason or 'not provided'}.",
                f"- Session status: {status.get('status', 'running')}.",
            ],
        )
        self._append_unique(sections, "Tool Results", f"- {timestamp} `{tool}` success={success} exit_code={exit_code} stdout={stdout or '-'} stderr={stderr or '-'}")
        if event.event_type in {"tool_result", "finish_patch_rejected", "invalid_llm_output"}:
            self._append_unique(sections, "Attempts", f"- {timestamp} `{tool}` reason={event.reason or '-'} result={outcome}.")
        if tool in {"read_code", "read_symbol_at", "search_code", "search_skill", "search_project_doc"}:
            self._append_unique(sections, "Evidence", f"- `{tool}` returned success={success}; summary: {stdout or stderr or '-'}")
        if tool in {"edit_code", "apply_test_patch"}:
            path = self._extract_path(event.arguments, result)
            self._append_unique(sections, "Files and Symbols", f"- Edited `{path or 'unknown'}` via `{tool}`; success={success}.")
        if tool in {"run_compile", "run_test", "create_pr", "feishu_tool"}:
            self._append_unique(sections, "Final Outcome", f"- {timestamp} `{tool}` success={success} exit_code={exit_code}.")
        self._replace_section(sections, "Next Actions", [self._next_action_line(tool, bool(success), status)])

    def _replace_section(self, sections: dict[str, list[str]], name: str, lines: list[str]) -> None:
        header = sections.get(name, [])
        description = next((line for line in header if line.startswith("_")), "")
        sections[name] = ([description, ""] if description else []) + lines

    def _append_unique(self, sections: dict[str, list[str]], name: str, line: str) -> None:
        lines = sections.setdefault(name, [])
        if line not in lines:
            lines.append(line)

    def _extract_path(self, arguments: dict[str, Any], result: dict[str, Any]) -> str:
        if isinstance(arguments, dict) and arguments.get("path"):
            return str(arguments.get("path"))
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        return str(data.get("path") or "")

    def _next_action_line(self, tool: str, success: bool, status: dict[str, Any]) -> str:
        if status.get("status") in {"passed", "reviewing"}:
            return "- Repair succeeded; wait for review/reflection if required."
        if not success:
            return f"- Inspect `{tool}` failure and adjust the next repair action."
        if tool == "edit_code":
            return "- Continue to finish_patch so compile and tests can run."
        if tool == "run_compile":
            return "- Run tests after successful compile."
        if tool == "run_test":
            return "- Create PR and request review after successful tests."
        return "- Continue the repair loop with the latest evidence."

    def _render_sections(self, sections: dict[str, list[str]]) -> str:
        chunks: list[str] = []
        for name in SECTION_NAMES:
            lines = sections.get(name) or []
            chunks.append(f"# {name}")
            chunks.extend(lines or [""])
            chunks.append("")
        return "\n".join(chunks).rstrip() + "\n"

    def _trim_summary(self, content: str, max_chars: int) -> str:
        marker = "\n# Tool Results\n"
        if marker not in content or len(content) <= max_chars:
            return content[:max_chars]
        head, tail = content.split(marker, 1)
        keep_tail = tail[-max(1000, max_chars // 3) :]
        trimmed = {
            "notice": "summary trimmed to configured max_summary_chars",
            "trimmed_at": datetime.utcnow().isoformat(timespec="seconds"),
        }
        return f"{head}{marker}_Older tool results were trimmed._\n\n```json\n{json.dumps(trimmed, ensure_ascii=False)}\n```\n{keep_tail}"[-max_chars:]
