from agent.tools.edit_code import EditCodeTool


def test_edit_code_applies_hunk_with_different_add_remove_counts(tmp_path):
    path = tmp_path / "UserService.java"
    path.write_text(
        "class UserService {\n"
        "    public String getNickname(Long userId) {\n"
        "        MallUser user = userRepository.findById(userId).orElseThrow();\n"
        "        // BUG-001: profileJson may be null, direct split causes NullPointerException.\n"
        "        return user.getProfileJson().split(\":\")[1];\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    tool = EditCodeTool()

    result = tool.run(
        {
            "path": str(path),
            "content": "--- a/src/main/java/com/fixflow/mall/service/UserService.java\n"
            "+++ b/src/main/java/com/fixflow/mall/service/UserService.java\n"
            "@@ -2,5 +2,8 @@\n"
            "     public String getNickname(Long userId) {\n"
            "         MallUser user = userRepository.findById(userId).orElseThrow();\n"
            "-        // BUG-001: profileJson may be null, direct split causes NullPointerException.\n"
            "-        return user.getProfileJson().split(\":\")[1];\n"
            "+        String profileJson = user.getProfileJson();\n"
            "+        if (profileJson == null) {\n"
            "+            return \"unknown\";\n"
            "+        }\n"
            "+        return profileJson.split(\":\")[1];\n"
            "     }",
        }
    )

    assert result.success is True
    assert "String profileJson = user.getProfileJson();" in path.read_text(encoding="utf-8")
    assert "return \"unknown\";" in path.read_text(encoding="utf-8")


def test_edit_code_rejects_raw_snippet_without_changing_file(tmp_path):
    path = tmp_path / "UserService.java"
    original = (
        "class UserService {\n"
        "    public String getNickname(Long userId) {\n"
        "        return user.getProfileJson().split(\":\")[1];\n"
        "    }\n"
        "}\n"
    )
    path.write_text(original, encoding="utf-8")
    tool = EditCodeTool()

    result = tool.run(
        {
            "path": str(path),
            "content": "    public String getNickname(Long userId) {\n"
            "        String profileJson = user.getProfileJson();\n"
            "        if (profileJson == null) {\n"
            "            return \"defaultNickname\";\n"
            "        }\n"
            "        return profileJson.split(\":\")[1];\n"
            "    }",
        }
    )

    assert result.success is False
    assert "requires a unified diff" in result.stderr_summary
    assert path.read_text(encoding="utf-8") == original


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
