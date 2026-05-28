from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from agent.config import AppConfig, FeishuKnowledgeConfig
from agent.main import main
from agent.rag.feishu_loader import FeishuKnowledgeClient, FeishuLoader, FeishuRawDocument
from agent.rag.knowledge_service import KnowledgeService


class FakeResponse:
    def __init__(self, payload: dict[str, object], status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self.payload


class FakeWikiSpaceSession:
    def __init__(self) -> None:
        self.post_payloads: list[dict[str, object]] = []
        self.get_urls: list[str] = []
        self.get_headers: list[dict[str, str]] = []

    def post(self, url: str, json: dict[str, object], headers: dict[str, str], timeout: int) -> FakeResponse:
        self.post_payloads.append(json)
        return FakeResponse({"code": 0, "tenant_access_token": "tenant-token"})

    def get(self, url: str, headers: dict[str, str], timeout: int) -> FakeResponse:
        self.get_urls.append(url)
        self.get_headers.append(headers)
        if "/wiki/v2/spaces/space-1/nodes" in url and "parent_node_token=folder-api" in url:
            return FakeResponse(
                {
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "node_token": "node-api",
                                "obj_token": "doc-api",
                                "obj_type": "docx",
                                "title": "Order API",
                                "has_child": False,
                                "updated_time": "2026-05-26T00:00:00Z",
                            }
                        ],
                        "has_more": False,
                    },
                }
            )
        if "/wiki/v2/spaces/space-1/nodes" in url:
            return FakeResponse(
                {
                    "code": 0,
                    "data": {
                        "items": [
                            {"node_token": "folder-api", "obj_type": "folder", "title": "API", "has_child": True},
                            {"node_token": "node-db", "obj_token": "doc-db", "obj_type": "docx", "title": "Order DB", "has_child": False},
                        ],
                        "has_more": False,
                    },
                }
            )
        if "/docx/v1/documents/doc-api/raw_content" in url:
            return FakeResponse({"code": 0, "data": {"content": "# Order API\n\namount must be positive"}})
        if "/docx/v1/documents/doc-db/raw_content" in url:
            return FakeResponse({"code": 0, "data": {"content": "# Order DB\n\norders table stores item ids"}})
        return FakeResponse({"code": 0, "data": {}})


def test_feishu_client_fetches_wiki_space_documents() -> None:
    session = FakeWikiSpaceSession()
    config = FeishuKnowledgeConfig(enabled=True, app_id="app-id", app_secret="app-secret", wiki_space_ids=["space-1"])
    client = FeishuKnowledgeClient(config, session=session)

    documents = client.fetch_documents()

    assert session.post_payloads[0]["app_id"] == "app-id"
    assert session.post_payloads[0]["app_secret"] == "app-secret"
    assert session.get_headers[0]["Authorization"] == "Bearer tenant-token"
    assert {doc.token for doc in documents} == {"doc-api", "doc-db"}
    api_doc = next(doc for doc in documents if doc.token == "doc-api")
    db_doc = next(doc for doc in documents if doc.token == "doc-db")
    assert api_doc.doc_type == "api_doc"
    assert api_doc.module == "order"
    assert "amount must be positive" in api_doc.content
    assert db_doc.doc_type == "db_doc"


class FakeFeishuClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    def fetch_documents(self) -> list[FeishuRawDocument]:
        self.calls += 1
        return [
            FeishuRawDocument(
                token="doc-secret-token",
                title="Order API",
                content=self.content,
                uri="feishu://wiki/space-1/node-api",
                updated_at="2026-05-26T00:00:00Z",
                module="order",
                doc_type="api_doc",
            )
        ]


def test_sync_feishu_docs_writes_rag_documents_and_chunks(tmp_path: Path) -> None:
    db_path = tmp_path / "rag.db"
    config = AppConfig()
    config.project.name = "mall-service"
    config.rag.db_path = str(db_path)
    config.rag.min_score = 0.0
    config.feishu_knowledge.enabled = True
    config.feishu_knowledge.app_id = "app-id"
    config.feishu_knowledge.app_secret = "app-secret"
    config.feishu_knowledge.wiki_space_ids = ["space-1"]
    fake_client = FakeFeishuClient("Authorization: Bearer abc123\npassword=secret\n# Order API\n\nfirstItemId order lookup")
    loader = FeishuLoader(config=config, client=fake_client)
    service = KnowledgeService(config=config, feishu_loader=loader)

    first = service.sync_feishu_docs()
    second = service.sync_feishu_docs()

    assert first == {"loaded": 1, "indexed": 1, "skipped": 0, "failed": 0}
    assert second == {"loaded": 1, "indexed": 0, "skipped": 1, "failed": 0}
    with sqlite3.connect(db_path) as conn:
        document = conn.execute("SELECT source, doc_type, project, module, title, metadata_json FROM rag_documents").fetchone()
        chunk = conn.execute("SELECT content, metadata_json FROM rag_chunks").fetchone()
    assert document[:5] == ("feishu", "api_doc", "mall-service", "order", "Order API")
    assert "doc-secret-token" not in document[5]
    assert "app-secret" not in document[5]
    assert "[REDACTED]" in chunk[0]
    assert "abc123" not in chunk[0]
    assert "password=secret" not in chunk[0]


class ExplodingFeishuClient:
    def fetch_documents(self) -> list[FeishuRawDocument]:
        raise AssertionError("disabled Feishu sync should not call the client")


def test_sync_feishu_docs_disabled_does_not_call_client(tmp_path: Path) -> None:
    config = AppConfig()
    config.rag.db_path = str(tmp_path / "rag.db")
    config.feishu_knowledge.enabled = False
    loader = FeishuLoader(config=config, client=ExplodingFeishuClient())
    service = KnowledgeService(config=config, feishu_loader=loader)

    assert service.sync_feishu_docs() == {"loaded": 0, "indexed": 0, "skipped": 0, "failed": 0}


class FailingFeishuLoader:
    def load(self):
        raise RuntimeError("network failed with app-secret")


def test_sync_feishu_docs_warning_redacts_app_secret(tmp_path: Path, capsys) -> None:
    config = AppConfig()
    config.rag.db_path = str(tmp_path / "rag.db")
    config.feishu_knowledge.enabled = True
    config.feishu_knowledge.app_secret = "app-secret"
    service = KnowledgeService(config=config, feishu_loader=FailingFeishuLoader())

    result = service.sync_feishu_docs()
    output = capsys.readouterr().out

    assert result["failed"] == 1
    assert "app-secret" not in output
    assert "[REDACTED]" in output


class BadWikiSpaceSession:
    def post(self, url: str, json: dict[str, object], headers: dict[str, str], timeout: int) -> FakeResponse:
        return FakeResponse({"code": 0, "tenant_access_token": "tenant-token-secret"})

    def get(self, url: str, headers: dict[str, str], timeout: int) -> FakeResponse:
        return FakeResponse(
            {"code": 131002, "msg": "param err", "data": {"tenant_access_token": "tenant-token-secret"}},
            status_code=400,
        )


def test_sync_feishu_docs_warning_includes_api_context_and_redacts_tokens(tmp_path: Path, capsys) -> None:
    config = AppConfig()
    config.rag.db_path = str(tmp_path / "rag.db")
    config.feishu_knowledge.enabled = True
    config.feishu_knowledge.app_id = "app-id"
    config.feishu_knowledge.app_secret = "app-secret"
    config.feishu_knowledge.wiki_space_ids = ["space-secret"]
    client = FeishuKnowledgeClient(config.feishu_knowledge, session=BadWikiSpaceSession())
    service = KnowledgeService(config=config, feishu_loader=FeishuLoader(config=config, client=client))

    result = service.sync_feishu_docs()
    output = capsys.readouterr().out

    assert result == {"loaded": 0, "indexed": 0, "skipped": 0, "failed": 1}
    assert "stage=list_wiki_nodes" in output
    assert "path=/wiki/v2/spaces/[SPACE]/nodes" in output
    assert "status=400" in output
    assert "feishu_code=131002" in output
    assert "feishu_msg=param err" in output
    assert "space-secret" not in output
    assert "tenant-token-secret" not in output
    assert "app-secret" not in output


class BadDocumentContentSession:
    def post(self, url: str, json: dict[str, object], headers: dict[str, str], timeout: int) -> FakeResponse:
        return FakeResponse({"code": 0, "tenant_access_token": "tenant-token-secret"})

    def get(self, url: str, headers: dict[str, str], timeout: int) -> FakeResponse:
        if "/wiki/v2/spaces/space-1/nodes" in url:
            return FakeResponse(
                {
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "node_token": "node-api-secret",
                                "obj_token": "doc-secret-token",
                                "obj_type": "docx",
                                "title": "Order API",
                                "has_child": False,
                            }
                        ],
                        "has_more": False,
                    },
                }
            )
        return FakeResponse({"code": 99991663, "msg": "permission denied"}, status_code=400)


def test_sync_feishu_docs_warning_redacts_document_token(tmp_path: Path, capsys) -> None:
    config = AppConfig()
    config.rag.db_path = str(tmp_path / "rag.db")
    config.feishu_knowledge.enabled = True
    config.feishu_knowledge.app_id = "app-id"
    config.feishu_knowledge.app_secret = "app-secret"
    config.feishu_knowledge.wiki_space_ids = ["space-1"]
    client = FeishuKnowledgeClient(config.feishu_knowledge, session=BadDocumentContentSession())
    service = KnowledgeService(config=config, feishu_loader=FeishuLoader(config=config, client=client))

    result = service.sync_feishu_docs()
    output = capsys.readouterr().out

    assert result["failed"] == 1
    assert "stage=fetch_doc_raw_content" in output
    assert "path=/docx/v1/documents/[TOKEN]/raw_content" in output
    assert "feishu_msg=permission denied" in output
    assert "doc-secret-token" not in output
    assert "node-api-secret" not in output
    assert "tenant-token-secret" not in output
    assert "app-secret" not in output


def test_rag_sync_feishu_cli_does_not_create_llm_or_expose_secret(tmp_path: Path, monkeypatch, capsys) -> None:
    from agent import main as main_module

    config = AppConfig()
    config.workspace = str(tmp_path)
    config.session.backend = "memory"
    config.llm.api_key = "configured-but-not-needed"
    config.feishu_knowledge.enabled = True
    config.feishu_knowledge.app_secret = "cli-secret"
    config.feishu_knowledge.wiki_space_ids = ["space-1"]

    class FakeKnowledgeService:
        def __init__(self, **kwargs) -> None:
            pass

        def sync_feishu_docs(self) -> dict[str, int]:
            return {"loaded": 1, "indexed": 1, "skipped": 0, "failed": 0}

    def fail_llm_client(**kwargs):
        raise AssertionError("rag-sync-feishu should not create an LLM client")

    monkeypatch.setattr(main_module, "load_config", lambda _: config)
    monkeypatch.setattr(main_module, "KnowledgeService", FakeKnowledgeService)
    monkeypatch.setattr(main_module, "OpenAICompatibleClient", fail_llm_client)

    exit_code = main(["rag-sync-feishu"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert '"indexed": 1' in output
    assert "cli-secret" not in output
