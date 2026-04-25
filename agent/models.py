from __future__ import annotations

"""定义系统中的核心数据模型。"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    """表示修复任务在生命周期中的状态。"""

    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"


@dataclass(slots=True)
class BugReport:
    """表示从外部系统接收到的一条缺陷报告。"""

    bug_id: str
    title: str = ""
    content: str = ""
    source: str = "unknown"
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(slots=True)
class ToolSpec:
    """描述一个工具的能力与权限要求。"""

    name: str
    description: str
    requires_approval: bool = False


@dataclass(slots=True)
class ToolCallResult:
    """封装工具执行后的统一返回结果。"""

    success: bool
    output: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RepairTask:
    """表示一条待执行或执行中的修复任务。"""

    bug_id: str
    status: TaskStatus = TaskStatus.PENDING
    summary: str = ""
    updated_at: datetime = field(default_factory=datetime.utcnow)
