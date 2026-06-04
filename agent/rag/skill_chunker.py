from __future__ import annotations

"""Create signal, fix, and validation child chunks for Skill parents."""

from hashlib import sha256
import json
import re

from agent.rag.models import KnowledgeChunk, KnowledgeDocument


SECTION_TO_CHILD = {
    "适用场景": "signal_chunk",
    "典型信号": "signal_chunk",
    "根因判断": "fix_chunk",
    "本次有效步骤": "fix_chunk",
    "人工修复关键点": "fix_chunk",
    "推荐排查步骤": "fix_chunk",
    "本次多余步骤": "validation_chunk",
    "遗漏点与错误": "validation_chunk",
    "避免事项": "validation_chunk",
    "验证方式": "validation_chunk",
}


class SkillChunker:
    """Split stable SkillGenerator sections while retaining full metadata."""

    def chunk(self, document: KnowledgeDocument) -> list[KnowledgeChunk]:
        sections = self._sections(document.content)
        grouped: dict[str, list[tuple[str, str]]] = {
            "signal_chunk": [],
            "fix_chunk": [],
            "validation_chunk": [],
        }
        for section_name, content in sections:
            child_type = SECTION_TO_CHILD.get(section_name, "signal_chunk")
            grouped[child_type].append((section_name, content))

        chunks: list[KnowledgeChunk] = []
        metadata_text = json.dumps(document.metadata, ensure_ascii=False, default=str, sort_keys=True)
        for child_type, items in grouped.items():
            if not items:
                continue
            section_names = [name for name, _ in items]
            body = "\n\n".join(f"## {name}\n{content}".strip() for name, content in items if content.strip())
            text = f"{document.title}\nSkill metadata: {metadata_text}\n\n{body}".strip()
            content_hash = f"sha256:{sha256(text.encode('utf-8')).hexdigest()}"
            chunks.append(
                KnowledgeChunk(
                    chunk_id=f"chunk-{sha256(f'{document.doc_id}:{child_type}:{content_hash}'.encode('utf-8')).hexdigest()[:24]}",
                    doc_id=document.doc_id,
                    source=document.source,
                    doc_type=document.doc_type,
                    project=document.project,
                    module=document.module,
                    title=document.title,
                    heading_path=section_names,
                    child_type=child_type,
                    section_name=", ".join(section_names),
                    content=text,
                    token_count=len(text.split()),
                    content_hash=content_hash,
                    metadata={**document.metadata, "uri": document.uri, "matched_sections": section_names},
                    updated_at=document.updated_at,
                )
            )
        return chunks

    def _sections(self, content: str) -> list[tuple[str, str]]:
        sections: list[tuple[str, list[str]]] = []
        current_name = "Skill metadata"
        current_lines: list[str] = []
        for line in content.splitlines():
            match = re.match(r"^##\s+(.+?)\s*$", line)
            if match:
                if current_lines:
                    sections.append((current_name, current_lines))
                current_name = match.group(1).strip()
                current_lines = []
                continue
            current_lines.append(line)
        if current_lines:
            sections.append((current_name, current_lines))
        return [(name, "\n".join(lines).strip()) for name, lines in sections if "\n".join(lines).strip()]
