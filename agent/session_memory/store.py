from __future__ import annotations

"""File-backed session memory storage."""

import re
from pathlib import Path

from agent.config import AppConfig


SUMMARY_TEMPLATE = """# Current State
_Current repair status, whether a patch exists, and the next intended action._

# Bug
_bug_id, exception type, request path, key error message, top business frame._

# Evidence
_Confirmed code facts, files/functions read, business constraints, RAG conclusions._

# Attempts
_Repairs attempted, patches applied, failures, rollbacks, and reasons._

# Tool Results
_Key tool results: search/read/edit/compile/test/rollback/PR summaries._

# Files and Symbols
_Important files, classes, methods, line numbers, and why they matter._

# Constraints
_Permission limits, denied commands, business rules, user or reviewer feedback._

# Next Actions
_What should happen next, and paths that should not be repeated._

# Final Outcome
_PR, compile/test, Feishu review, and final status._
"""


class SessionMemoryStore:
    """Read and write per-bug summary.md files."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        root = config.session_memory.root_dir.strip() or config.session.root_dir
        self.root_dir = Path(root)

    def summary_path(self, bug_id: str) -> Path:
        safe_bug_id = self._safe_path_name(bug_id)
        return self.root_dir / safe_bug_id / "session-memory" / "summary.md"

    def ensure_summary(self, bug_id: str) -> Path:
        path = self.summary_path(bug_id)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(SUMMARY_TEMPLATE, encoding="utf-8")
        return path

    def read_summary(self, bug_id: str) -> str:
        path = self.ensure_summary(bug_id)
        return path.read_text(encoding="utf-8")

    def write_summary(self, bug_id: str, content: str) -> Path:
        path = self.summary_path(bug_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(content, encoding="utf-8")
        temp_path.replace(path)
        return path

    def _safe_path_name(self, value: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
        return safe or "unknown"
