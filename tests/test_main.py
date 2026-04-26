"""验证命令行入口。"""

from pathlib import Path

from agent.main import main


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

    monkeypatch.setattr(main_module, "load_config", lambda _: main_module.load_config("config.example.yaml"))
    monkeypatch.setattr(main_module, "ToolRegistry", lambda: type("R", (), {"get": lambda self, name: None, "register": lambda self, tool: None, "list_tools": lambda self: []})())
    monkeypatch.setattr(main_module, "PermissionGuard", lambda: object())
    monkeypatch.setattr(main_module, "TaskStore", lambda: object())
    monkeypatch.setattr(main_module, "SessionStore", lambda: object())
    monkeypatch.setattr(main_module, "SkillStore", lambda: object())
    monkeypatch.setattr(main_module, "TaskManager", lambda task_store: type("M", (), {"create_task": lambda self, bug_id: None, "update_status": lambda self, bug_id, status: None})())
    monkeypatch.setattr(main_module, "RepairAgent", lambda **kwargs: type("A", (), {"repair": lambda self, bug_id: None})())
    monkeypatch.setattr(main_module, "IngestionPipeline", lambda **kwargs: type("P", (), {"process": fake_process})())
    monkeypatch.setattr(main_module, "Doctor", lambda config: type("D", (), {"run": lambda self: "doctor"})())
    monkeypatch.setattr(main_module, "ReflectionSubAgent", lambda **kwargs: type("R", (), {"reflect": lambda self, bug_id, result: "reflection"})())

    exit_code = main(["repair", "--bug-id", "BUG-1", "--raw-log-path", str(log_path), "--project", "demo"])

    assert exit_code == 0
