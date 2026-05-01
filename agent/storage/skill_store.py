from __future__ import annotations

"""提供技能内容的存储能力，支持内存缓存和磁盘持久化。"""

from pathlib import Path


class SkillStore:
    """保存技能文本，支持从磁盘 skills/ 目录启动时自动加载。"""

    def __init__(self, skills_dir: str | Path | None = None) -> None:
        """初始化技能存储。skills_dir 为 skills/ 目录路径。"""
        self._items: dict[str, str] = {}
        self._skills_dir: Path | None = Path(skills_dir) if skills_dir else None

    def get(self, key: str) -> str | None:
        """根据键读取技能文本。"""
        return self._items.get(key)

    def put(self, key: str, value: str) -> None:
        """写入或覆盖指定键的技能文本。"""
        self._items[key] = value

    def load_from_disk(self) -> int:
        """从磁盘 skills/ 目录加载所有 SKILL.md，返回加载数量。"""
        if self._skills_dir is None or not self._skills_dir.is_dir():
            return 0

        loaded = 0
        for skill_dir in sorted(self._skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.is_file():
                continue
            skill_name = skill_dir.name
            content = skill_md.read_text(encoding="utf-8")
            if content.strip():
                self._items[skill_name] = content
                loaded += 1
        return loaded
