from __future__ import annotations

"""Small deterministic compressors for tool events."""

import json
from typing import Any

from agent.config import AppConfig
from agent.models import ToolResult


class ToolResultCompressor:
    """Reduce tool result payloads before writing session memory."""

    def __init__(self, config: AppConfig) -> None:
        self.max_field_chars = max(200, config.session_memory.max_field_chars)
        self.max_event_chars = max(1000, config.session_memory.max_event_chars)

    def compress_result(self, result: ToolResult) -> dict[str, Any]:
        payload = {
            "tool": result.tool,
            "success": result.success,
            "exit_code": result.exit_code,
            "stdout_summary": self._clip(result.stdout_summary),
            "stderr_summary": self._clip(result.stderr_summary),
            "data": self._compress_value(result.data),
            "artifacts": result.artifacts[:10],
        }
        return self._fit_payload(payload)

    def compress_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        value = self._compress_value(arguments)
        return value if isinstance(value, dict) else {}

    def _compress_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._clip(value)
        if isinstance(value, list):
            return [self._compress_value(item) for item in value[:20]]
        if isinstance(value, dict):
            return {str(key): self._compress_value(item) for key, item in list(value.items())[:30]}
        return value

    def _fit_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = json.dumps(payload, ensure_ascii=False, default=str)
        if len(text) <= self.max_event_chars:
            return payload
        compact = dict(payload)
        compact["data"] = {"omitted": True, "reason": "tool result too large for session memory"}
        compact["stdout_summary"] = self._clip(str(compact.get("stdout_summary", "")), self.max_field_chars // 2)
        compact["stderr_summary"] = self._clip(str(compact.get("stderr_summary", "")), self.max_field_chars // 2)
        return compact

    def _clip(self, text: str, limit: int | None = None) -> str:
        max_chars = limit or self.max_field_chars
        if len(text) <= max_chars:
            return text
        return f"{text[:max_chars]}...[truncated {len(text) - max_chars} chars]"
