from __future__ import annotations

"""生成反思后沉淀的 skill 内容。"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from agent.models import SkillMeta


@dataclass(slots=True)
class SkillArtifact:
    """表示生成的 skill 产物。"""

    meta: SkillMeta
    markdown: str
    skill_dir: Path
    markdown_path: Path
    meta_path: Path


class SkillGenerator:
    """把总结内容转成 skill markdown 和 metadata。"""

    def build(self, *, name: str, description: str, source_bug_id: str, body: str, skill_dir: Path) -> SkillArtifact:
        """构造 skill 产物。"""
        skill_dir.mkdir(parents=True, exist_ok=True)
        meta = SkillMeta(name=name, description=description, source_bug_id=source_bug_id, created_at=datetime.utcnow())
        markdown = f"# {name}\n\n{body}\n"
        markdown_path = skill_dir / "SKILL.md"
        meta_path = skill_dir / "skill.meta.json"
        return SkillArtifact(meta=meta, markdown=markdown, skill_dir=skill_dir, markdown_path=markdown_path, meta_path=meta_path)
