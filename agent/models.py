from __future__ import annotations

"""定义系统中的核心业务数据模型。"""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    """表示修复任务在生命周期中的状态。"""

    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    REVIEWING = "reviewing"


class ToolSpec(BaseModel):
    """描述工具的名称、输入、权限与执行器信息。"""

    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    permission: str = "READ_ONLY"
    executor: str = "local"


class ProjectConfig(BaseModel):
    """描述缺陷所属项目的运行上下文信息。"""

    name: str
    repo: str = ""
    language: str = "java"
    base_branch: str = "main"


class BugEvent(BaseModel):
    """表示从日志或消息系统接收到的一条缺陷事件。"""

    bug_id: str
    source: str
    project: str
    title: str
    exception_type: str
    message: str
    traceback: str = ""
    frames: list[StackFrame] = Field(default_factory=list)
    request_path: str = ""
    request_method: str = ""
    top_business_frame: str = ""
    fingerprint: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class BugReport(BaseModel):
    """表示外部系统输入的原始缺陷报告。"""

    bug_id: str
    title: str = ""
    content: str = ""
    source: str = "unknown"
    created_at: datetime = Field(default_factory=datetime.utcnow)


class StackFrame(BaseModel):
    """表示异常堆栈中的单个调用帧。"""

    file_path: str
    function_name: str
    line_number: int
    code_line: str = ""
    module_name: str = ""
    is_business_code: bool = False


class RepairTask(BaseModel):
    """表示一条自动修复任务的完整状态。"""

    id: str = Field(default_factory=lambda: str(uuid4()))
    bug_id: str
    project: str
    status: TaskStatus = TaskStatus.PENDING
    retry_count: int = 0
    max_retry: int = 3
    agent_branch: str = ""
    base_branch: str = "main"
    pr_url: str = ""
    session_path: str = ""


class ToolCallRecord(BaseModel):
    """记录一次工具调用的输入、输出与时间信息。"""

    task_id: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: datetime | None = None
    success: bool | None = None


class ToolResult(BaseModel):
    """表示统一格式的工具执行结果 JSON 结构。"""

    tool: str
    success: bool
    exit_code: int
    stdout_summary: str = ""
    stderr_summary: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[str] = Field(default_factory=list)


class LegacyCompactionState(BaseModel):
    """记录一次修复任务中的 Legacy Full Compact 运行状态。"""

    compact_count: int = 0
    consecutive_failures: int = 0
    tokens_before: int = 0
    tokens_after: int = 0
    last_summary_path: str = ""
    last_transcript_path: str = ""
    last_error: str = ""
    last_compacted_at: datetime | None = None


class LegacyCompactionResult(BaseModel):
    """描述一次 compact 检查或执行的结果。"""

    compacted: bool = False
    blocked: bool = False
    reason: str = ""
    messages: list[dict[str, Any]] = Field(default_factory=list)
    tokens_before: int = 0
    tokens_after: int = 0
    dropped_round_count: int = 0
    dropped_message_count: int = 0
    restored_file_count: int = 0
    ptl_retry_count: int = 0
    summary_input_tokens: int = 0
    summary_output_tokens: int = 0
    summary_path: str = ""
    transcript_path: str = ""


class ReviewEvent(BaseModel):
    """表示一次人工审核或自动审核事件。"""

    task_id: str
    reviewer: str
    decision: str
    comment: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


class SkillMeta(BaseModel):
    """表示反思沉淀后生成的技能元信息。"""

    name: str
    version: str = "1.0.0"
    schema_version: int = 2
    description: str = ""
    source_bug_id: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    project: str = ""
    module: str = ""
    exception_type: str = ""
    top_business_frame: str = ""
    class_name: str = ""
    method_name: str = ""
    root_cause_type: str = ""
    fix_pattern: str = ""
    skill_type: str = "legacy_unclassified"
    use_types: list[str] = Field(default_factory=list)
    has_human_diff: bool = False
    has_agent_diff: bool = False


class ReviewDecision(str, Enum):
    """表示人工审核事件的结果。"""

    REVIEW_PASSED = "review_passed"
    REVIEW_FAILED = "review_failed"
