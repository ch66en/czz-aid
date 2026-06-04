from __future__ import annotations

"""High-level local knowledge service for repair-time RAG."""

from pathlib import Path
import re
from typing import Any

from agent.config import AppConfig
from agent.ingestion.sanitizer import Sanitizer
from agent.models import BugEvent
from agent.rag.chunker import MarkdownChunker
from agent.rag.context_synthesizer import ContextSynthesizer
from agent.rag.embedder import EmbeddingProvider, build_embedding_provider
from agent.rag.feishu_loader import FeishuLoader
from agent.rag.hybrid_retriever import HybridRetriever
from agent.rag.local_doc_loader import LOCAL_DOC_TYPES, LocalDocLoader
from agent.rag.models import RagRepairContext, RagStatus, RetrievalResult
from agent.rag.repair_context_resolver import RepairContextResolver
from agent.rag.repair_query_builder import RepairQueryBuilder
from agent.rag.retriever import Retriever
from agent.rag.skill_chunker import SkillChunker
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
        skill_chunker: SkillChunker | None = None,
        skill_loader: SkillLoader | None = None,
        local_doc_loader: LocalDocLoader | None = None,
        feishu_loader: FeishuLoader | None = None,
        retriever: Retriever | None = None,
        context_synthesizer: ContextSynthesizer | None = None,
    ) -> None:
        self.config = config
        self.sanitizer = Sanitizer()
        self.skills_dir = Path(skills_dir) if skills_dir is not None else Path(config.workspace) / "skills"
        self.docs_dir = Path(docs_dir) if docs_dir is not None else Path(config.workspace) / "docs"
        db_path = config.rag.db_path or config.session.db_path
        self.vector_store = vector_store or SQLiteVectorStore(db_path)
        self.embedder = embedder or build_embedding_provider(config)
        self.chunker = chunker or MarkdownChunker()
        self.skill_chunker = skill_chunker or SkillChunker()
        self.skill_loader = skill_loader or SkillLoader(self.skills_dir, default_project=config.project.name)
        self.local_doc_loader = local_doc_loader or LocalDocLoader(self.docs_dir, default_project=config.project.name)
        self.feishu_loader = feishu_loader or FeishuLoader(config=config)
        self.retriever = retriever or Retriever(self.vector_store, self.embedder)
        self.context_resolver = RepairContextResolver(config.rag.module_aliases)
        self.query_builder = RepairQueryBuilder()
        self.hybrid_retriever = HybridRetriever(self.vector_store, self.embedder, config.rag.retrieval)
        self.context_synthesizer = context_synthesizer or ContextSynthesizer(config.rag.context_synthesizer)

    def index_skills(self) -> int:
        documents = self.skill_loader.load()
        indexed = self._index_documents(documents, chunker=self.skill_chunker)
        self.vector_store.delete_documents_not_in(source="skill", active_doc_ids={item.doc_id for item in documents})
        return indexed

    def index_local_docs(self) -> int:
        documents = self.local_doc_loader.load()
        indexed = self._index_documents(documents, chunker=self.chunker)
        self.vector_store.delete_documents_not_in(source="local_doc", active_doc_ids={item.doc_id for item in documents})
        return indexed

    def rebuild_index(self) -> dict[str, int]:
        self.vector_store.clear()
        summary = {
            "skills": self.index_skills(),
            "local_docs": self.index_local_docs(),
            "feishu_docs": 0,
        }
        if self.config.feishu_knowledge.enabled:
            summary["feishu_docs"] = self.sync_feishu_docs().get("indexed", 0)
        self.vector_store.rebuild_fts()
        return summary

    def index_status(self) -> dict[str, Any]:
        return self.vector_store.index_status()

    def set_synthesizer_llm_client(self, llm_client: Any | None) -> None:
        self.context_synthesizer.llm_client = llm_client

    def pre_retrieve_for_bug(
        self,
        bug_event: BugEvent,
        session: dict[str, Any],
    ) -> tuple[RagRepairContext, RagStatus]:
        if not self.config.rag.enabled:
            return RagRepairContext(missing_info=["RAG is disabled."]), RagStatus(status="disabled")
        status = RagStatus()
        try:
            resolved = self.context_resolver.resolve(bug_event, session)
            query = self.query_builder.build(resolved)
        except Exception as exc:
            return RagRepairContext(missing_info=["Repair context could not be normalized."]), RagStatus(
                status="failed",
                degraded_stages=["query_builder"],
                reasons=[self._safe_error(exc)],
                fallback_strategies=["empty_rag_context"],
            )
        try:
            skills = self.hybrid_retriever.retrieve_skills(query, status)
        except Exception as exc:
            skills = []
            self._degrade(status, "skill_retrieval", exc, "project_docs_only")
        try:
            project_docs = self.hybrid_retriever.retrieve_project_docs(query, status)
        except Exception as exc:
            project_docs = []
            self._degrade(status, "project_doc_retrieval", exc, "skills_only")
        candidates = sorted([*skills, *project_docs], key=lambda item: item.score, reverse=True)[: self.config.rag.retrieval.candidate_top_n]
        selected_skill_ids = {item.doc_id for item in skills}
        selected_doc_chunks = {item.chunk_id for item in project_docs}
        skills = [item for item in candidates if item.doc_id in selected_skill_ids and item.doc_type == "skill"]
        project_docs = [item for item in candidates if item.chunk_id in selected_doc_chunks and item.doc_type != "skill"]
        status.candidate_count = len(candidates)
        if any(item.metadata.get("skill_type") == "legacy_unclassified" for item in skills):
            self._degrade(status, "skill_metadata", ValueError("legacy_unclassified Skill selected"), "debug_hint_only")
        if any(item.metadata.get("authority") == "unclassified" for item in project_docs):
            self._degrade(status, "document_authority", ValueError("unclassified project document selected"), "soft_hint_only")
        failed_stages = set(status.degraded_stages)
        if not candidates and (
            {"bm25", "vector"}.issubset(failed_stages)
            or {"skill_retrieval", "project_doc_retrieval"}.issubset(failed_stages)
        ):
            status.status = "failed"
            status.fallback_strategies.append("empty_rag_context")
        try:
            context = self.context_synthesizer.synthesize(query, skills, project_docs, status)
        except Exception as exc:
            self._degrade(status, "context_synthesizer", exc, "deterministic_synthesizer")
            context = self.context_synthesizer.deterministic_fallback(query, skills, project_docs)
        status.selected_source_count = len(context.selected_sources)
        if status.degraded_stages and status.status == "success":
            status.status = "degraded"
        return context, status

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

    def _index_documents(self, documents: list[Any], *, chunker: Any | None = None) -> int:
        indexed = 0
        selected_chunker = chunker or self.chunker
        for document in documents:
            if self.vector_store.get_document_content_hash(document.doc_id) == document.content_hash:
                continue
            chunks = selected_chunker.chunk(document)
            if not chunks:
                continue
            embeddings = self.embedder.embed_batch([chunk.content for chunk in chunks])
            for chunk, embedding in zip(chunks, embeddings):
                chunk.embedding = embedding
            dimension = len(embeddings[0]) if embeddings else 0
            self.vector_store.set_index_identity(
                embedding_provider=self.config.rag.embedding_provider or type(self.embedder).__name__,
                embedding_model=self.config.rag.embedding_model or type(self.embedder).__name__,
                embedding_dimension=dimension,
            )
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
        message = self.sanitizer.sanitize(str(exc))
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
        return " ".join(message.split())[:500]

    def _degrade(self, status: RagStatus, stage: str, exc: Exception, fallback: str) -> None:
        status.status = "degraded"
        if stage not in status.degraded_stages:
            status.degraded_stages.append(stage)
        status.reasons.append(self._safe_error(exc)[:240])
        if fallback not in status.fallback_strategies:
            status.fallback_strategies.append(fallback)
