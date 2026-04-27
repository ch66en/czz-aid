"""验证命令行入口。"""

from pathlib import Path
from types import SimpleNamespace

from agent.main import main
from agent.main import _format_repair_output


class DummyResult:
    def __str__(self) -> str:
        return "ok"


def test_repair_reads_log_from_path(tmp_path: Path, monkeypatch) -> None:
    log_path = tmp_path / "error.log"
    log_path.write_text("java.lang.RuntimeException: boom", encoding="utf-8")

    def fake_process(*args, **kwargs):
        assert kwargs["raw_text"] == "java.lang.RuntimeException: boom"
        return type("Result", (), {"repair_result": None})()

    from agent import main as main_module

    captured = {}
    real_load_config = main_module.config_module.load_config

    monkeypatch.setattr(main_module, "load_config", lambda _: real_load_config("config.example.yaml"))
    monkeypatch.setattr(main_module.config_module, "load_config", lambda _: real_load_config("config.example.yaml"))
    monkeypatch.setattr(main_module, "ToolRegistry", lambda: type("R", (), {"get": lambda self, name: None, "register": lambda self, tool: None, "list_tools": lambda self: []})())
    monkeypatch.setattr(main_module, "PermissionGuard", lambda: object())
    monkeypatch.setattr(main_module, "TaskStore", lambda: object())
    monkeypatch.setattr(main_module, "SessionStore", lambda: object())
    monkeypatch.setattr(main_module, "SkillStore", lambda: object())
    monkeypatch.setattr(main_module, "TaskManager", lambda task_store: type("M", (), {"create_task": lambda self, bug_id: None, "update_status": lambda self, bug_id, status: None})())
    monkeypatch.setattr(main_module, "OpenAICompatibleClient", lambda config: type("LLM", (), {})())
    monkeypatch.setattr(main_module, "RepairAgent", lambda **kwargs: captured.setdefault("repair_kwargs", kwargs) or type("A", (), {"repair": lambda self, bug_id: None})())
    monkeypatch.setattr(main_module, "IngestionPipeline", lambda **kwargs: type("P", (), {"process": fake_process})())
    monkeypatch.setattr(main_module, "Doctor", lambda config: type("D", (), {"run": lambda self: "doctor"})())
    monkeypatch.setattr(main_module, "ReflectionSubAgent", lambda **kwargs: captured.setdefault("reflection_kwargs", kwargs) or type("R", (), {"reflect": lambda self, bug_id, result: "reflection"})())

    exit_code = main(["repair", "--bug-id", "BUG-1", "--raw-log-path", str(log_path), "--project", "demo"])

    assert exit_code == 0
    assert captured["repair_kwargs"]["llm_client"] is not None
    assert captured["reflection_kwargs"]["llm_client"] is captured["repair_kwargs"]["llm_client"]


def test_repair_output_is_compact() -> None:
    result = SimpleNamespace(
        repair_result=SimpleNamespace(
            status="failed",
            message="auto repair exhausted",
            task=SimpleNamespace(bug_id="BUG-1", pr_url=""),
            last_result=SimpleNamespace(tool="edit_code", success=False, stderr_summary="", stdout_summary="x" * 500),
        )
    )

    output = _format_repair_output(result)

    assert "repair failed: BUG-1" in output
    assert "last_tool: edit_code success=False" in output
    assert len(output) < 400
    assert "x" * 500 not in output
