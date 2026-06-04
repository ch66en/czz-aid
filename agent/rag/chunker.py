from __future__ import annotations

"""Chunk knowledge documents into small retrieval units."""

from hashlib import sha256
import re

from agent.rag.models import KnowledgeChunk, KnowledgeDocument


class MarkdownChunker:
    """Split Markdown-like text by headings, with a simple size guard."""

    def __init__(self, max_chars: int = 1800) -> None:
        self.max_chars = max_chars

    def chunk(self, document: KnowledgeDocument) -> list[KnowledgeChunk]:
        sections = self._sections(document.content)
        chunks: list[KnowledgeChunk] = []
        for heading_path, content in sections:
            for part in self._split_large(content):
                text = part.strip()
                if not text:
                    continue
                section_name = heading_path[-1] if heading_path else document.title
                retrieval_text = self._retrieval_text(document.title, heading_path, text)
                content_hash = self._hash(retrieval_text)
                index = len(chunks)
                chunks.append(
                    KnowledgeChunk(
                        chunk_id=self._chunk_id(document.doc_id, index, content_hash),
                        doc_id=document.doc_id,
                        source=document.source,
                        doc_type=document.doc_type,
                        project=document.project,
                        module=document.module,
                        title=document.title,
                        heading_path=heading_path,
                        child_type="document_chunk",
                        section_name=section_name,
                        content=retrieval_text,
                        token_count=len(retrieval_text.split()),
                        content_hash=content_hash,
                        metadata={**document.metadata, "uri": document.uri, "heading_path": heading_path},
                        updated_at=document.updated_at,
                    )
                )
        return chunks

    def _retrieval_text(self, title: str, heading_path: list[str], content: str) -> str:
        labels = [title, *heading_path]
        prefix = " / ".join(dict.fromkeys(label for label in labels if label))
        return f"{prefix}\n\n{content}".strip() if prefix else content

    def _sections(self, content: str) -> list[tuple[list[str], str]]:
        lines = content.splitlines()
        sections: list[tuple[list[str], list[str]]] = []
        current_path: list[str] = []
        current_lines: list[str] = []
        for line in lines:
            match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
            if match:
                if current_lines:
                    sections.append((current_path[:], current_lines))
                level = len(match.group(1))
                title = match.group(2).strip()
                current_path = current_path[: level - 1] + [title]
                current_lines = [line]
                continue
            current_lines.append(line)
        if current_lines:
            sections.append((current_path[:], current_lines))
        if not sections:
            return [([], content)]
        return [(heading_path, "\n".join(section_lines)) for heading_path, section_lines in sections]

    def _split_large(self, content: str) -> list[str]:
        if len(content) <= self.max_chars:
            return [content]
        parts: list[str] = []
        buffer: list[str] = []
        size = 0
        for paragraph in re.split(r"(\n\s*\n)", content):
            if size + len(paragraph) > self.max_chars and buffer:
                parts.append("".join(buffer))
                buffer = []
                size = 0
            buffer.append(paragraph)
            size += len(paragraph)
        if buffer:
            parts.append("".join(buffer))
        return parts

    def _chunk_id(self, doc_id: str, index: int, content_hash: str) -> str:
        return f"chunk-{sha256(f'{doc_id}:{index}:{content_hash}'.encode('utf-8')).hexdigest()[:24]}"

    def _hash(self, text: str) -> str:
        return f"sha256:{sha256(text.encode('utf-8')).hexdigest()}"
