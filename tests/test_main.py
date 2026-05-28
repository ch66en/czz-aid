"""验证命令行入口。"""

from pathlib import Path

from agent.config import AppConfig
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

    config = AppConfig()
    config.session.backend = "memory"
    monkeypatch.setattr(main_module, "load_config", lambda _: config)
    monkeypatch.setattr(main_module, "ToolRegistry", lambda: type("R", (), {"get": lambda self, name: None, "register": lambda self, tool: None, "list_tools": lambda self: []})())
    monkeypatch.setattr(main_module, "PermissionGuard", lambda config=None: object())
    monkeypatch.setattr(main_module, "TaskStore", lambda: object())
    monkeypatch.setattr(main_module, "SessionStore", lambda: object())
    monkeypatch.setattr(main_module, "SkillStore", lambda skills_dir=None: type("S", (), {"load_from_disk": lambda self: 0})())
    monkeypatch.setattr(main_module, "TaskManager", lambda task_store: type("M", (), {"create_task": lambda self, bug_id: None, "update_status": lambda self, bug_id, status: None})())
    monkeypatch.setattr(main_module, "RepairAgent", lambda **kwargs: type("A", (), {"repair": lambda self, bug_id: None})())
    monkeypatch.setattr(main_module, "IngestionPipeline", lambda **kwargs: type("P", (), {"process": fake_process})())
    monkeypatch.setattr(main_module, "Doctor", lambda config: type("D", (), {"run": lambda self: "doctor"})())
    monkeypatch.setattr(main_module, "ReflectionSubAgent", lambda **kwargs: type("R", (), {"reflect": lambda self, bug_id, result: "reflection"})())

    exit_code = main(["repair", "--bug-id", "BUG-1", "--raw-log-path", str(log_path), "--project", "demo"])

    assert exit_code == 0


def test_watch_starts_log_watcher_with_config(monkeypatch) -> None:
    from agent import main as main_module

    config = AppConfig()
    config.session.backend = "memory"
    config.project.name = "order-service"
    config.agent.watch_paths = ["./runtime/app.log"]
    created: dict[str, object] = {}

    class FakeWatcher:
        def __init__(self, **kwargs) -> None:
            created.update(kwargs)

        def watch(self) -> str:
            created["watched"] = True
            return "watching"

    class FakeReviewServer:
        def __init__(self, host, port, reflection) -> None:
            created["review_server"] = (host, port, reflection)

        def start(self) -> bool:
            created["review_started"] = True
            return True

        def stop(self) -> None:
            created["review_stopped"] = True

    monkeypatch.setattr(main_module, "load_config", lambda _: config)
    monkeypatch.setattr(main_module, "ToolRegistry", lambda: type("R", (), {"get": lambda self, name: None, "register": lambda self, tool: None, "list_tools": lambda self: []})())
    monkeypatch.setattr(main_module, "PermissionGuard", lambda config=None: object())
    monkeypatch.setattr(main_module, "TaskStore", lambda: object())
    monkeypatch.setattr(main_module, "SessionStore", lambda: object())
    monkeypatch.setattr(main_module, "SkillStore", lambda skills_dir=None: type("S", (), {"load_from_disk": lambda self: 0})())
    monkeypatch.setattr(main_module, "TaskManager", lambda task_store: type("M", (), {"create_task": lambda self, bug_id: None, "update_status": lambda self, bug_id, status: None})())
    monkeypatch.setattr(main_module, "RepairAgent", lambda **kwargs: type("A", (), {"repair": lambda self, bug_id: None})())
    monkeypatch.setattr(main_module, "IngestionPipeline", lambda **kwargs: object())
    monkeypatch.setattr(main_module, "Doctor", lambda config: type("D", (), {"run": lambda self: "doctor"})())
    monkeypatch.setattr(main_module, "ReflectionSubAgent", lambda **kwargs: type("R", (), {"reflect": lambda self, bug_id, result: "reflection"})())
    monkeypatch.setattr(main_module, "LogWatcher", FakeWatcher)
    monkeypatch.setattr(main_module, "ReviewCallbackServer", FakeReviewServer)

    exit_code = main(["watch"])

    assert exit_code == 0
    assert created["paths"] == ["./runtime/app.log"]
    assert created["project"] == "order-service"
    assert created["watched"] is True
    assert created["review_started"] is True
    assert created["review_stopped"] is True


def test_main_uses_sqlite_storage_when_configured(tmp_path: Path, monkeypatch) -> None:
    from agent import main as main_module

    config = AppConfig()
    config.session.backend = "sqlite"
    config.session.db_path = str(tmp_path / "agent.db")
    created: dict[str, object] = {}

    class FakeSQLiteTaskStore:
        def __init__(self, db_path: str) -> None:
            created["task_db_path"] = db_path

    class FakeSQLiteSessionStore:
        def __init__(self, db_path: str) -> None:
            created["session_db_path"] = db_path

    class FakeSQLiteDedupStore:
        def __init__(self, db_path: str) -> None:
            created["dedup_db_path"] = db_path

    monkeypatch.setattr(main_module, "load_config", lambda _: config)
    monkeypatch.setattr(main_module, "ToolRegistry", lambda: type("R", (), {"get": lambda self, name: None, "register": lambda self, tool: None, "list_tools": lambda self: []})())
    monkeypatch.setattr(main_module, "PermissionGuard", lambda config=None: object())
    monkeypatch.setattr(main_module, "SQLiteTaskStore", FakeSQLiteTaskStore)
    monkeypatch.setattr(main_module, "SQLiteSessionStore", FakeSQLiteSessionStore)
    monkeypatch.setattr(main_module, "SQLiteDedupStore", FakeSQLiteDedupStore)
    monkeypatch.setattr(main_module, "SkillStore", lambda skills_dir=None: type("S", (), {"load_from_disk": lambda self: 0})())
    monkeypatch.setattr(main_module, "TaskManager", lambda task_store: type("M", (), {"create_task": lambda self, bug_id: None, "update_status": lambda self, bug_id, status: None})())
    monkeypatch.setattr(main_module, "RepairAgent", lambda **kwargs: object())
    monkeypatch.setattr(main_module, "IngestionPipeline", lambda **kwargs: object())
    monkeypatch.setattr(main_module, "Doctor", lambda config: type("D", (), {"run": lambda self: "doctor"})())
    monkeypatch.setattr(main_module, "ReflectionSubAgent", lambda **kwargs: object())

    exit_code = main(["doctor"])

    assert exit_code == 0
    assert created["task_db_path"] == str(tmp_path / "agent.db")
    assert created["session_db_path"] == str(tmp_path / "agent.db")
    assert created["dedup_db_path"] == str(tmp_path / "agent.db")


def test_rag_index_skills_does_not_create_llm_client(tmp_path: Path, monkeypatch) -> None:
    from agent import main as main_module

    config = AppConfig()
    config.workspace = str(tmp_path)
    config.session.backend = "memory"
    config.llm.api_key = "configured-but-not-needed"
    created: dict[str, object] = {}

    class FakeKnowledgeService:
        def __init__(self, **kwargs) -> None:
            created["knowledge_service"] = kwargs

        def index_skills(self) -> int:
            return 2

        def index_local_docs(self) -> int:
            return 3

    def fail_llm_client(**kwargs):
        raise AssertionError("rag-index-skills should not create an LLM client")

    monkeypatch.setattr(main_module, "load_config", lambda _: config)
    monkeypatch.setattr(main_module, "KnowledgeService", FakeKnowledgeService)
    monkeypatch.setattr(main_module, "OpenAICompatibleClient", fail_llm_client)

    exit_code = main(["rag-index-skills"])

    assert exit_code == 0
    assert created["knowledge_service"]["config"] is config


def test_rag_index_docs_does_not_create_llm_client(tmp_path: Path, monkeypatch) -> None:
    from agent import main as main_module

    config = AppConfig()
    config.workspace = str(tmp_path)
    config.session.backend = "memory"
    config.llm.api_key = "configured-but-not-needed"
    created: dict[str, object] = {}

    class FakeKnowledgeService:
        def __init__(self, **kwargs) -> None:
            created["knowledge_service"] = kwargs

        def index_local_docs(self) -> int:
            return 4

    def fail_llm_client(**kwargs):
        raise AssertionError("rag-index-docs should not create an LLM client")

    monkeypatch.setattr(main_module, "load_config", lambda _: config)
    monkeypatch.setattr(main_module, "KnowledgeService", FakeKnowledgeService)
    monkeypatch.setattr(main_module, "OpenAICompatibleClient", fail_llm_client)

    exit_code = main(["rag-index-docs"])

    assert exit_code == 0
    assert created["knowledge_service"]["config"] is config
