"""验证核心数据模型的字段与默认值。"""

from agent.models import BugEvent, RepairTask, ReviewEvent, SkillMeta, StackFrame, TaskStatus, ToolCallRecord, ToolResult


def test_bug_event_contains_required_fields() -> None:
    """缺陷事件应包含约定的核心字段。"""
    event = BugEvent(
        bug_id="BUG-1001",
        source="feishu",
        project="order-service",
        title="创建订单失败",
        exception_type="NullPointerException",
        message="order is null",
        request_path="/api/orders",
        request_method="POST",
        top_business_frame="com.demo.order.OrderService.create",
        fingerprint="fp-123",
    )

    assert event.bug_id == "BUG-1001"
    assert event.project == "order-service"
    assert event.exception_type == "NullPointerException"
    assert event.top_business_frame == "com.demo.order.OrderService.create"
    assert event.fingerprint == "fp-123"
    assert event.created_at is not None


def test_repair_task_contains_required_fields() -> None:
    """修复任务应包含约定字段及合理默认值。"""
    task = RepairTask(
        bug_id="BUG-1001",
        project="order-service",
        session_path="./data/sessions/BUG-1001",
    )

    assert task.id
    assert task.bug_id == "BUG-1001"
    assert task.project == "order-service"
    assert task.status == TaskStatus.PENDING
    assert task.retry_count == 0
    assert task.max_retry == 3
    assert task.base_branch == "main"
    assert task.pr_url == ""
    assert task.session_path == "./data/sessions/BUG-1001"


def test_tool_result_uses_unified_json_shape() -> None:
    """工具结果应符合统一 JSON 结构。"""
    result = ToolResult(
        tool="run_command",
        success=True,
        exit_code=0,
        stdout_summary="build ok",
        stderr_summary="",
        data={"command": "pytest"},
        artifacts=["./reports/test.xml"],
    )

    assert result.model_dump() == {
        "tool": "run_command",
        "success": True,
        "exit_code": 0,
        "stdout_summary": "build ok",
        "stderr_summary": "",
        "data": {"command": "pytest"},
        "artifacts": ["./reports/test.xml"],
    }


def test_other_models_can_be_constructed() -> None:
    """其余核心模型应可正常实例化。"""
    frame = StackFrame(
        file_path="src/order_service.py",
        function_name="create_order",
        line_number=42,
        code_line="raise ValueError('bad order')",
        module_name="order_service",
        is_business_code=True,
    )
    record = ToolCallRecord(task_id="task-1", tool="read_log", arguments={"path": "app.log"})
    review = ReviewEvent(task_id="task-1", reviewer="feishu-user", decision="approved", comment="looks good")
    skill = SkillMeta(name="null-check-fix", description="为空对象补充判空逻辑", source_bug_id="BUG-1001")

    assert frame.is_business_code is True
    assert record.tool == "read_log"
    assert review.decision == "approved"
    assert skill.source_bug_id == "BUG-1001"
