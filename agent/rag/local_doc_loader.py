from __future__ import annotations

"""Load local project documents as RAG documents."""

from datetime import datetime
from hashlib import sha256
import re
from pathlib import Path
from typing import Any

import yaml

from agent.rag.models import KnowledgeDocument


LOCAL_DOC_TYPE_BY_DIR = {
    "product": "product_doc",
    "design": "design_doc",
    "api": "api_doc",
    "db": "db_doc",
    "error-code": "error_code_doc",
    "module": "module_doc",
}

LOCAL_DOC_TYPES = list(LOCAL_DOC_TYPE_BY_DIR.values())


class LocalDocLoader:
    """Scan workspace/docs and load Markdown/text business documents."""

    def __init__(self, docs_dir: str | Path, default_project: str = "") -> None:
        self.docs_dir = Path(docs_dir)
        self.default_project = default_project

    def load(self) -> list[KnowledgeDocument]:
        if not self.docs_dir.is_dir():
            return []
        documents: list[KnowledgeDocument] = []
        for directory_name, doc_type in LOCAL_DOC_TYPE_BY_DIR.items():
            root = self.docs_dir / directory_name
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*")):
                if not path.is_file() or path.suffix.lower() not in {".md", ".txt"}:
                    continue
                document = self._load_file(path, doc_type)
                if document is not None:
                    documents.append(document)
        return documents

    def _load_file(self, path: Path, doc_type: str) -> KnowledgeDocument | None:
        try:
            raw_content = path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not raw_content:
            return None

        metadata, content = self._split_front_matter(raw_content)
        content = content.strip()
        if not content:
            return None

        project = str(metadata.get("project") or self.default_project)
        module = str(metadata.get("module") or "")
        title = str(metadata.get("title") or self._first_heading(content) or path.stem)
        relative = self._relative_uri(path)
        content_hash = f"sha256:{sha256(content.encode('utf-8')).hexdigest()}"
        return KnowledgeDocument(
            doc_id=f"local_doc:{relative}",
            source="local_doc",
            doc_type=doc_type,
            project=project,
            module=module,
            title=title,
            content=content,
            uri=str(path),
            updated_at=datetime.utcfromtimestamp(path.stat().st_mtime).isoformat(),
            content_hash=content_hash,
            metadata={**metadata, "doc_path": str(path), "relative_path": relative},
        )

    def _split_front_matter(self, content: str) -> tuple[dict[str, Any], str]:
        match = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)(.*)$", content, flags=re.S)
        if not match:
            return {}, content
        try:
            metadata = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            return {}, content
        if not isinstance(metadata, dict):
            metadata = {}
        return metadata, match.group(2)

    def _first_heading(self, content: str) -> str:
        for line in content.splitlines():
            match = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
            if match:
                return match.group(1).strip()
        return ""

    def _relative_uri(self, path: Path) -> str:
        try:
            return path.relative_to(self.docs_dir).as_posix()
        except ValueError:
            return path.as_posix()
