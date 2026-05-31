from __future__ import annotations

"""Persist an append-only transcript that remains available after compaction."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.config import AppConfig
from agent.ingestion.sanitizer import Sanitizer


class CompactTranscript:
    """Store the full repair conversation as sanitized JSON Lines records.

    Active ``llm_messages`` may be shortened after compaction. This transcript is
    append-only, so an LLM can recover exact older details with ``read_code`` by
    using the absolute path included in the compact marker.
    """

    def __init__(self, config: AppConfig, sanitizer: Sanitizer | None = None) -> None:
        self.config = config
        self.sanitizer = sanitizer or Sanitizer()

    def path_for(self, bug_id: str) -> Path:
        """Return the stable absolute transcript path for one repair task."""
        safe_bug_id = re.sub(r"[^A-Za-z0-9._-]+", "-", bug_id).strip("-") or "bug"
        return (Path(self.config.session.root_dir) / safe_bug_id / "transcript.jsonl").resolve()

    def append_message(self, bug_id: str, message: dict[str, Any], *, source: str) -> Path:
        """Append one model-visible message without changing the active context."""
        return self.append_event(
            bug_id,
            "message",
            {
                "source": source,
                "message": message,
            },
        )

    def append_event(self, bug_id: str, event: str, data: dict[str, Any]) -> Path:
        """Append one sanitized audit record and return the transcript path."""
        path = self.path_for(bug_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "data": self._sanitize_value(data),
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str))
            handle.write("\n")
        return path

    def ensure_exists(self, bug_id: str, messages: list[dict[str, Any]]) -> Path:
        """Create a usable transcript when the compactor is called directly.

        Normal RepairAgent runs append messages as they happen. The fallback is
        useful for resumed sessions, direct library callers, and unit tests.
        """
        path = self.path_for(bug_id)
        if path.exists():
            return path
        for message in messages:
            self.append_message(bug_id, message, source="compact_snapshot")
        if not path.exists():
            # Even an empty conversation should leave a readable audit file.
            self.append_event(bug_id, "compact_snapshot", {"messages": []})
        return path

    def _sanitize_value(self, value: Any) -> Any:
        """Sanitize string leaves while preserving valid JSON structure."""
        if isinstance(value, str):
            return self.sanitizer.sanitize(value)
        if isinstance(value, dict):
            return {str(key): self._sanitize_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._sanitize_value(item) for item in value]
        if isinstance(value, tuple):
            return [self._sanitize_value(item) for item in value]
        return value
