from __future__ import annotations

"""Load local SKILL.md files as RAG documents."""

from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from agent.models import SkillMeta
from agent.rag.models import KnowledgeDocument


SKILL_USE_TYPES = {
    "review_passed": ["recommended_fix", "validation_hint"],
    "review_failed": ["human_fix_hint", "avoid_pattern", "validation_hint"],
    "legacy_unclassified": ["debug_hint"],
}


class SkillLoader:
    """Scan workspace/skills/<skill-name>/SKILL.md and optional metadata."""

    def __init__(self, skills_dir: str | Path, default_project: str = "") -> None:
        self.skills_dir = Path(skills_dir)
        self.default_project = default_project

    def load(self) -> list[KnowledgeDocument]:
        if not self.skills_dir.is_dir():
            return []
        documents: list[KnowledgeDocument] = []
        for skill_dir in sorted(self.skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.is_file():
                continue
            content = skill_md.read_text(encoding="utf-8").strip()
            if not content:
                continue
            metadata = self._normalize_meta(self._load_meta(skill_dir / "skill.meta.json"), fallback_name=skill_dir.name)
            name = str(metadata.get("name") or skill_dir.name)
            title = str(metadata.get("description") or name)
            project = str(metadata.get("project") or self.default_project)
            module = str(metadata.get("module") or "")
            content_hash = f"sha256:{sha256(content.encode('utf-8')).hexdigest()}"
            documents.append(
                KnowledgeDocument(
                    doc_id=f"skill:{skill_dir.name}",
                    source="skill",
                    doc_type="skill",
                    project=project,
                    module=module,
                    title=title,
                    content=content,
                    uri=str(skill_md),
                    updated_at=datetime.utcfromtimestamp(skill_md.stat().st_mtime).isoformat(),
                    content_hash=content_hash,
                    metadata={**metadata, "skill_name": name, "skill_dir": str(skill_dir)},
                )
            )
        return documents

    def _load_meta(self, path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _normalize_meta(self, raw: dict[str, Any], *, fallback_name: str) -> dict[str, Any]:
        skill_type = str(raw.get("skill_type") or "")
        schema_version = raw.get("schema_version")
        if skill_type not in {"review_passed", "review_failed"} or schema_version != 2:
            skill_type = "legacy_unclassified"
        use_types = raw.get("use_types")
        if not isinstance(use_types, list) or skill_type == "legacy_unclassified":
            use_types = SKILL_USE_TYPES[skill_type]
        allowed_use_types = {"recommended_fix", "human_fix_hint", "avoid_pattern", "debug_hint", "validation_hint"}
        normalized_use_types = [str(item) for item in use_types if str(item) in allowed_use_types]
        if skill_type != "review_passed":
            normalized_use_types = [item for item in normalized_use_types if item != "recommended_fix"]
        try:
            meta = SkillMeta.model_validate(
                {
                    **raw,
                    "name": str(raw.get("name") or fallback_name),
                    "schema_version": 2,
                    "skill_type": skill_type,
                    "use_types": normalized_use_types or SKILL_USE_TYPES[skill_type],
                }
            )
        except Exception:
            meta = SkillMeta(name=fallback_name, skill_type="legacy_unclassified", use_types=SKILL_USE_TYPES["legacy_unclassified"])
        return {**raw, **meta.model_dump(mode="json")}
