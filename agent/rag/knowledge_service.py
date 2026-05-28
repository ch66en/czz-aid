from __future__ import annotations

"""High-level local knowledge service for repair-time RAG."""

from pathlib import Path
import re
from typing import Any

from agent.config import AppConfig
from agent.models import BugEvent
from agent.rag.chunker import MarkdownChunker
from agent.rag.embedder import EmbeddingProvider, build_embedding_provider
from agent.rag.feishu_loader import FeishuLoader
from agent.rag.local_doc_loader import LOCAL_DOC_TYPES, LocalDocLoader
from agent.rag.models import RetrievalResult
from agent.rag.retriever import Retriever
from agent.rag.skill_loader import SkillLoader
from agent.rag.vector_store import SQLiteVectorStore
from agent.ui import warning as ui_warning


class KnowledgeService:
    """Index local skills and retrieve the most relevant ones for a bug."""

    def __init__(
        self,
        *,
        config: AppConfig,
        skills_dir: str | Path | None = None,
        docs_dir: str | Path | None = None,
        vector_store: SQLiteVectorStore | None = None,
        embedder: EmbeddingProvider | None = None,
        chunker: MarkdownChunker | None = None,
        skill_loader: SkillLoader | None = None,
        local_doc_loader: LocalDocLoader | None = None,
        feishu_loader: FeishuLoader | None = None,
        retriever: Retriever | None = None,
    ) -> None:
        self.config = config
        self.skills_dir = Path(skills_dir) if skills_dir is not None else Path(config.workspace) / "skills"
        self.docs_dir = Path(docs_dir) if docs_dir is not None else Path(config.workspace) / "docs"
        db_path = config.rag.db_path or config.session.db_path
        self.vector_store = vector_store or SQLiteVectorStore(db_path)
        self.embedder = embedder or build_embedding_provider(config)
        self.chunker = chunker or MarkdownChunker()
        self.skill_loader = skill_loader or SkillLoader(self.skills_dir, default_project=config.project.name)
        self.local_doc_loader = local_doc_loader or LocalDocLoader(self.docs_dir, default_project=config.project.name)
        self.feishu_loader = feishu_loader or FeishuLoader(config=config)
        self.retriever = retriever or Retriever(self.vector_store, self.embedder)

    def index_skills(self) -> int:
        documents = self.skill_loader.load()
        return self._index_documents(documents)

    def index_local_docs(self) -> int:
        documents = self.local_doc_loader.load()
        return self._index_documents(documents)

    def sync_feishu_docs(self) -> dict[str, int]:
        summary = {"loaded": 0, "indexed": 0, "skipped": 0, "failed": 0}
        if not self.config.feishu_knowledge.enabled:
            return summary
        try:
            documents = self.feishu_loader.load()
        except Exception as exc:
            summary["failed"] = 1
            ui_warning(f"Feishu knowledge sync failed: {self._safe_error(exc)}")
            return summary

        summary["loaded"] = len(documents)
        for document in documents:
            try:
                existing_hash = self.vector_store.get_document_content_hash(document.doc_id)
                if existing_hash and existing_hash == document.content_hash:
                    summary["skipped"] += 1
                    continue
                if self._index_documents([document]):
                    summary["indexed"] += 1
            except Exception as exc:
                summary["failed"] += 1
                ui_warning(f"Feishu document sync skipped: {self._safe_error(exc)}")
        return summary

    def _index_documents(self, documents: list[Any]) -> int:
        indexed = 0
        for document in documents:
            chunks = self.chunker.chunk(document)
            if not chunks:
                continue
            embeddings = self.embedder.embed_batch([chunk.content for chunk in chunks])
            for chunk, embedding in zip(chunks, embeddings):
                chunk.embedding = embedding
            self.vector_store.upsert_document(document, chunks)
            indexed += 1
        return indexed

    def retrieve_skills_for_bug(self, bug_event: BugEvent, top_k: int | None = None) -> list[RetrievalResult]:
        query = self._query_for_bug(bug_event)
        return self.retriever.retrieve(
            query=query,
            project=bug_event.project,
            doc_type="skill",
            top_k=top_k or self.config.rag.top_k_skills,
            min_score=self.config.rag.min_score,
        )

    def retrieve_project_docs_for_bug(self, bug_event: BugEvent, session: dict[str, Any] | None = None, top_k: int = 5) -> list[RetrievalResult]:
        query = self._query_for_bug(bug_event, session=session)
        return self.retriever.retrieve(
            query=query,
            project=bug_event.project,
            doc_type=LOCAL_DOC_TYPES,
            top_k=top_k,
            min_score=self.config.rag.min_score,
        )

    def _query_for_bug(self, bug_event: BugEvent, session: dict[str, Any] | None = None) -> str:
        parts: list[str] = [
            bug_event.exception_type,
            bug_event.message,
            bug_event.top_business_frame,
        ]
        for frame in bug_event.frames:
            parts.extend([frame.file_path, frame.function_name, frame.module_name])
        if isinstance(session, dict):
            frame_contexts = session.get("frame_contexts", [])
            if isinstance(frame_contexts, list):
                for context in frame_contexts:
                    if not isinstance(context, dict):
                        continue
                    parts.extend(
                        [
                            str(context.get("filePath", "")),
                            str(context.get("symbol", "")),
                            str(context.get("target", "")),
                            str(context.get("module", "")),
                        ]
                    )
        return " ".join(part for part in parts if part).strip()

    def _safe_error(self, exc: Exception) -> str:
        message = str(exc)
        secret = self.config.feishu_knowledge.app_secret.strip()
        if secret:
            message = message.replace(secret, "[REDACTED]")
        message = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", message, flags=re.IGNORECASE)
        message = re.sub(
            r"(tenant_access_token[\"'=:\s]+)([^,\"'\s}]+)",
            r"\1[REDACTED]",
            message,
            flags=re.IGNORECASE,
        )
        message = re.sub(
            r"(app_secret[\"'=:\s]+)([^,\"'\s}]+)",
            r"\1[REDACTED]",
            message,
            flags=re.IGNORECASE,
        )
        message = re.sub(r"/docx/v1/documents/[^/?\s]+/raw_content", "/docx/v1/documents/[TOKEN]/raw_content", message)
        message = re.sub(r"/doc/v2/[^/?\s]+/raw_content", "/doc/v2/[TOKEN]/raw_content", message)
        message = re.sub(r"/wiki/v2/spaces/[^/?\s]+/nodes", "/wiki/v2/spaces/[SPACE]/nodes", message)
        return message
