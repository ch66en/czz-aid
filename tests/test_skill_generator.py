from __future__ import annotations

import json

from agent.reflection.skill_generator import SkillGenerator


def test_skill_generator_renders_fixed_sections(tmp_path) -> None:
    body = json.dumps(
        {
            "applicable_scenario": "Java NPE",
            "typical_signals": ["NullPointerException", "top frame in service"],
            "root_cause": "missing null guard",
            "recommended_steps": ["read top frame", "run tests"],
            "avoid_patterns": ["skip validation"],
            "validation_steps": ["mvn test"],
        },
        ensure_ascii=False,
    )

    artifact = SkillGenerator().build(name="skill-demo", description="demo", source_bug_id="BUG-1", body=body, skill_dir=tmp_path / "skill-demo")

    assert artifact.markdown_path.name == "SKILL.md"
    assert "## 适用场景" in artifact.markdown
    assert "Java NPE" in artifact.markdown
    assert "## 推荐排查步骤" in artifact.markdown
    assert "- read top frame" in artifact.markdown
    assert "## 验证方式" in artifact.markdown
