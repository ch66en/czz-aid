from __future__ import annotations

"""Models for session memory and context compaction."""

from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class SessionMemoryState(BaseModel):
    bug_id: str
    memory_path: str = ""
    initialized: bool = False
    last_summarized_history_index: int = 0
    last_summarized_message_index: int = 0
    last_summarized_tool_call_index: int = 0
    session_revision: int = 0
    summary_revision: int = 0
    update_count: int = 0
    in_progress: bool = False
    last_update_at: datetime | None = None
    last_error: str = ""


class MemoryUpdateEvent(BaseModel):
    bug_id: str
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    event_type: str = "tool_result"
    tool_call_index: int = 0
    message_index: int = 0
    history_index: int = 0
    session_revision: int = 0
    tool: str = ""
    reason: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    session_status: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class CompactionState(BaseModel):
    bug_id: str
    compacted: bool = False
    compact_count: int = 0
    last_compacted_message_index: int = 0
    last_compacted_history_index: int = 0
    tokens_before: int = 0
    tokens_after: int = 0
    usage_ratio_before: float = 0.0
    usage_ratio_after: float = 0.0
    last_reason: str = ""
    last_compacted_at: datetime | None = None
    last_error: str = ""


class CompactionResult(BaseModel):
    compacted: bool
    reason: str = ""
    messages: list[dict[str, Any]] = Field(default_factory=list)
    tokens_before: int = 0
    tokens_after: int = 0
    usage_ratio_before: float = 0.0
    usage_ratio_after: float = 0.0
