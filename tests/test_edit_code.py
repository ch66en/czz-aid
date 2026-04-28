from agent.tools.edit_code import EditCodeTool


def test_edit_code_fails_when_diff_context_does_not_match(tmp_path):
    path = tmp_path / "Demo.java"
    path.write_text("class Demo { int x = 1; }\n", encoding="utf-8")
    tool = EditCodeTool()

    result = tool.run(
        {
            "path": str(path),
            "content": "--- a/Demo.java\n+++ b/Demo.java\n@@\n-        missing();\n+        fixed();",
        }
    )

    assert result.success is False
    assert "patch context not found" in result.stderr_summary
    assert path.read_text(encoding="utf-8") == "class Demo { int x = 1; }\n"
