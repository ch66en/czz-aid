from __future__ import annotations

"""生成反思后沉淀的 skill 内容。"""

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any

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
        sections = self._sections(body)
        markdown = self._render_markdown(name, source_bug_id, sections)
        markdown_path = skill_dir / "SKILL.md"
        meta_path = skill_dir / "skill.meta.json"
        return SkillArtifact(meta=meta, markdown=markdown, skill_dir=skill_dir, markdown_path=markdown_path, meta_path=meta_path)

    def _sections(self, body: str) -> dict[str, str]:
        parsed = self._parse_json_body(body)
        if parsed:
            return {
                "适用场景": self._value(parsed, "applicable_scenario", "适用场景"),
                "典型信号": self._value(parsed, "typical_signals", "典型信号"),
                "根因判断": self._value(parsed, "root_cause", "根因"),
                "本次有效步骤": self._value(parsed, "useful_steps", "有效步骤"),
                "本次多余步骤": self._value(parsed, "useless_steps", "多余步骤"),
                "遗漏点与错误": self._value(parsed, "agent_mistakes", "missing_steps", "遗漏点"),
                "人工修复关键点": self._value(parsed, "human_fix_key_points", "人工修复关键点"),
                "推荐排查步骤": self._value(parsed, "recommended_steps", "推荐步骤"),
                "避免事项": self._value(parsed, "avoid_patterns", "避免事项"),
                "验证方式": self._value(parsed, "validation_steps", "验证方式"),
            }
        return self._parse_text_body(body)

    def _parse_json_body(self, body: str) -> dict[str, Any]:
        text = body.strip()
        if not text:
            return {}
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.S)
            if not match:
                return {}
            try:
                value = json.loads(match.group(0))
            except json.JSONDecodeError:
                return {}
        return value if isinstance(value, dict) else {}

    def _parse_text_body(self, body: str) -> dict[str, str]:
        labels = {
            "适用场景": "适用场景",
            "典型信号": "典型信号",
            "根因判断": "根因",
            "本次有效步骤": "有效步骤",
            "本次多余步骤": "多余步骤",
            "遗漏点与错误": "遗漏点",
            "人工修复关键点": "人工修复关键点",
            "推荐排查步骤": "推荐步骤",
            "避免事项": "避免事项",
            "验证方式": "验证",
        }
        sections = {name: "" for name in labels}
        for section, label in labels.items():
            match = re.search(rf"{re.escape(label)}[：:]\s*(.*?)(?=(?:适用场景|典型信号|根因|有效步骤|多余步骤|遗漏点|人工修复关键点|推荐步骤|避免事项|验证)[：:]|$)", body, flags=re.S)
            if match:
                sections[section] = match.group(1).strip(" \n。")
        if not any(sections.values()):
            sections["适用场景"] = body.strip()
        return sections

    def _value(self, data: dict[str, Any], *keys: str) -> str:
        for key in keys:
            if key not in data:
                continue
            value = data[key]
            if isinstance(value, list):
                return "\n".join(f"- {item}" for item in value)
            if isinstance(value, dict):
                return json.dumps(value, ensure_ascii=False, indent=2)
            return str(value)
        return ""

    def _render_markdown(self, name: str, source_bug_id: str, sections: dict[str, str]) -> str:
        ordered = [
            "适用场景",
            "典型信号",
            "根因判断",
            "本次有效步骤",
            "本次多余步骤",
            "遗漏点与错误",
            "人工修复关键点",
            "推荐排查步骤",
            "避免事项",
            "验证方式",
        ]
        lines = [f"# {name}", "", f"来源 Bug：`{source_bug_id}`", ""]
        for section in ordered:
            content = sections.get(section, "").strip() or "暂无"
            lines.extend([f"## {section}", "", content, ""])
        return "\n".join(lines).rstrip() + "\n"
