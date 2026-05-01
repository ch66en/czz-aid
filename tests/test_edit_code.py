from agent.config import AppConfig
from agent.tools.edit_code import EditCodeTool


def _configured_tool_with_source(tmp_path, name="Demo.java", content="class Demo { int x = 1; }\n"):
    project_root = tmp_path / "project"
    source = project_root / "src" / "main" / "java" / name
    source.parent.mkdir(parents=True)
    source.write_text(content, encoding="utf-8")
    config = AppConfig()
    config.project.root = str(project_root)
    return EditCodeTool(config), source, config


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


def test_edit_code_resolves_relative_path_from_project_root(tmp_path):
    tool, source, _config = _configured_tool_with_source(tmp_path)

    result = tool.run(
        {
            "path": "src/main/java/Demo.java",
            "content": "--- a/src/main/java/Demo.java\n"
            "+++ b/src/main/java/Demo.java\n"
            "@@\n"
            "-class Demo { int x = 1; }\n"
            "+class Demo { int x = 2; }",
        }
    )

    assert result.success is True
    assert result.data["path"] == str(source)
    assert source.read_text(encoding="utf-8") == "class Demo { int x = 2; }\n"


def test_edit_code_records_original_content_and_file_existed(tmp_path):
    path = tmp_path / "Demo.java"
    original = "class Demo { int x = 1; }\n"
    path.write_text(original, encoding="utf-8")
    tool = EditCodeTool()

    result = tool.run(
        {
            "path": str(path),
            "content": "--- a/Demo.java\n+++ b/Demo.java\n@@\n-class Demo { int x = 1; }\n+class Demo { int x = 2; }",
        }
    )

    assert result.success is True
    assert result.data["original_content"] == original
    assert result.data["file_existed"] is True


def test_edit_code_records_file_existed_false_for_new_file_without_config(tmp_path):
    path = tmp_path / "New.java"
    tool = EditCodeTool()

    result = tool.run(
        {
            "path": str(path),
            "content": "--- /dev/null\n+++ b/New.java\n@@\n+class New {}",
        }
    )

    assert result.success is True
    assert result.data["original_content"] == ""
    assert result.data["file_existed"] is False


def test_edit_code_skips_lint_when_not_configured(tmp_path):
    tool, path, config = _configured_tool_with_source(tmp_path)
    config.project.lint_command = ""

    result = tool.run(
        {
            "path": str(path),
            "content": "--- a/src/main/java/Demo.java\n+++ b/src/main/java/Demo.java\n@@\n-class Demo { int x = 1; }\n+class Demo { int x = 2; }",
        }
    )

    assert result.success is True
    assert result.data["lint_passed"] is None
    assert result.data["lint_output"] == ""


def test_edit_code_succeeds_when_lint_passes(tmp_path):
    tool, path, config = _configured_tool_with_source(tmp_path)
    config.project.lint_command = "python -c \"print('ok')\""

    result = tool.run(
        {
            "path": str(path),
            "content": "--- a/src/main/java/Demo.java\n+++ b/src/main/java/Demo.java\n@@\n-class Demo { int x = 1; }\n+class Demo { int x = 2; }",
        }
    )

    assert result.success is True
    assert result.data["lint_passed"] is True
    assert "ok" in result.data["lint_output"]


def test_edit_code_reverts_file_when_lint_fails(tmp_path):
    original = "class Demo { int x = 1; }\n"
    tool, path, config = _configured_tool_with_source(tmp_path, content=original)
    config.project.lint_command = "python -c \"import sys; sys.exit(1)\""

    result = tool.run(
        {
            "path": str(path),
            "content": "--- a/src/main/java/Demo.java\n+++ b/src/main/java/Demo.java\n@@\n-class Demo { int x = 1; }\n+class Demo { int x = 2; }",
        }
    )

    assert result.success is False
    assert "lint failed" in result.stderr_summary
    assert result.data["lint_passed"] is False
    assert path.read_text(encoding="utf-8") == original


def test_edit_code_rejects_new_file_when_configured(tmp_path):
    path = tmp_path / "project" / "src" / "main" / "java" / "New.java"
    config = AppConfig()
    config.project.root = str(tmp_path / "project")
    config.project.lint_command = "python -c \"import sys; sys.exit(1)\""
    tool = EditCodeTool(config)

    result = tool.run(
        {
            "path": str(path),
            "content": "--- /dev/null\n+++ b/src/main/java/New.java\n@@\n+class New {}",
        }
    )

    assert result.success is False
    assert "new files are not allowed" in result.stderr_summary
    assert path.exists() is False


def test_edit_code_handles_missing_lint_command(tmp_path):
    original = "class Demo { int x = 1; }\n"
    tool, path, config = _configured_tool_with_source(tmp_path, content=original)
    config.project.lint_command = "nonexistent_lint_tool_12345"

    result = tool.run(
        {
            "path": str(path),
            "content": "--- a/src/main/java/Demo.java\n+++ b/src/main/java/Demo.java\n@@\n-class Demo { int x = 1; }\n+class Demo { int x = 2; }",
        }
    )

    assert result.success is False
    assert "lint failed" in result.stderr_summary
    assert "not found" in result.data["lint_output"]
    assert path.read_text(encoding="utf-8") == original


def test_edit_code_rejects_configured_path_outside_project_root(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    outside = tmp_path / "outside" / "Demo.java"
    outside.parent.mkdir()
    outside.write_text("class Demo { int x = 1; }\n", encoding="utf-8")
    config = AppConfig()
    config.project.root = str(project_root)
    tool = EditCodeTool(config)

    result = tool.run(
        {
            "path": str(outside),
            "content": "--- a/src/main/java/Demo.java\n+++ b/src/main/java/Demo.java\n@@\n-class Demo { int x = 1; }\n+class Demo { int x = 2; }",
        }
    )

    assert result.success is False
    assert result.stderr_summary == "path is outside project root"
    assert outside.read_text(encoding="utf-8") == "class Demo { int x = 1; }\n"


def test_edit_code_rejects_config_files_even_inside_project(tmp_path):
    project_root = tmp_path / "project"
    config_file = project_root / "src" / "main" / "java" / "application.yaml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("debug: true\n", encoding="utf-8")
    config = AppConfig()
    config.project.root = str(project_root)
    tool = EditCodeTool(config)

    result = tool.run(
        {
            "path": str(config_file),
            "content": "--- a/src/main/java/application.yaml\n+++ b/src/main/java/application.yaml\n@@\n-debug: true\n+debug: false",
        }
    )

    assert result.success is False
    assert "Java source files" in result.stderr_summary
    assert config_file.read_text(encoding="utf-8") == "debug: true\n"


def test_edit_code_rejects_diff_header_mismatch(tmp_path):
    tool, path, _config = _configured_tool_with_source(tmp_path)

    result = tool.run(
        {
            "path": str(path),
            "content": "--- a/src/main/java/Other.java\n+++ b/src/main/java/Other.java\n@@\n-class Demo { int x = 1; }\n+class Demo { int x = 2; }",
        }
    )

    assert result.success is False
    assert result.stderr_summary == "diff header path does not match target path"
    assert path.read_text(encoding="utf-8") == "class Demo { int x = 1; }\n"


def test_edit_code_rejects_large_patch(tmp_path):
    original = "".join(f"class Line{i} {{}}\n" for i in range(60))
    tool, path, _config = _configured_tool_with_source(tmp_path, content=original)
    diff = (
        "--- a/src/main/java/Demo.java\n"
        "+++ b/src/main/java/Demo.java\n"
        "@@\n"
        + "".join(f"-class Line{i} {{}}\n" for i in range(60))
        + "".join(f"+class NewLine{i} {{}}\n" for i in range(60))
    )

    result = tool.run({"path": str(path), "content": diff})

    assert result.success is False
    assert "adds too many lines" in result.stderr_summary or "deletes too many lines" in result.stderr_summary
    assert path.read_text(encoding="utf-8") == original
