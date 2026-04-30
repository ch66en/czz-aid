from __future__ import annotations

from agent.reflection.diff_analyzer import DiffAnalyzer


def test_diff_analyzer_reports_file_overlap_and_missing_context() -> None:
    agent_diff = (
        "diff --git a/src/A.java b/src/A.java\n"
        "--- a/src/A.java\n"
        "+++ b/src/A.java\n"
        "-old\n"
        "+agent\n"
        "diff --git a/src/AgentOnly.java b/src/AgentOnly.java\n"
        "+++ b/src/AgentOnly.java\n"
        "+agent only\n"
    )
    human_diff = (
        "diff --git a/src/A.java b/src/A.java\n"
        "--- a/src/A.java\n"
        "+++ b/src/A.java\n"
        "-old\n"
        "+human\n"
        "diff --git a/src/HumanOnly.java b/src/HumanOnly.java\n"
        "+++ b/src/HumanOnly.java\n"
        "+human only\n"
    )

    result = DiffAnalyzer().analyze(agent_diff, human_diff)

    assert result.common_files == ["src/A.java"]
    assert result.agent_only_files == ["src/AgentOnly.java"]
    assert result.human_only_files == ["src/HumanOnly.java"]
    assert result.agent_added_lines == 2
    assert result.human_removed_lines == 1
    assert "human_only=1" in result.summary
    assert result.observations
