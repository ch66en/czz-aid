from agent.tools.edit_code import EditCodeTool
from agent.config import AppConfig


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
    project_root = tmp_path / "project"
    source = project_root / "src" / "main" / "java" / "Demo.java"
    source.parent.mkdir(parents=True)
    source.write_text("class Demo { int x = 1; }\n", encoding="utf-8")
    config = AppConfig()
    config.project.root = str(project_root)
    tool = EditCodeTool(config)

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
    """编辑成功时应在 data 中记录原文件内容和文件是否已存在。"""
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


def test_edit_code_records_file_existed_false_for_new_file(tmp_path):
    """新建文件时 file_existed 应为 False，original_content 为空字符串。"""
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
    """未配置 lint_command 时，编辑成功且 lint_passed 为 None。"""
    path = tmp_path / "Demo.java"
    path.write_text("class Demo { int x = 1; }\n", encoding="utf-8")
    config = AppConfig()
    config.project.lint_command = ""
    tool = EditCodeTool(config)

    result = tool.run(
        {
            "path": str(path),
            "content": "--- a/Demo.java\n+++ b/Demo.java\n@@\n-class Demo { int x = 1; }\n+class Demo { int x = 2; }",
        }
    )

    assert result.success is True
    assert result.data["lint_passed"] is None
    assert result.data["lint_output"] == ""


def test_edit_code_succeeds_when_lint_passes(tmp_path):
    """lint 通过时编辑成功，lint_passed 为 True。"""
    path = tmp_path / "Demo.java"
    path.write_text("class Demo { int x = 1; }\n", encoding="utf-8")
    config = AppConfig()
    config.project.root = str(tmp_path)
    config.project.lint_command = "python -c \"print('ok')\""
    tool = EditCodeTool(config)

    result = tool.run(
        {
            "path": str(path),
            "content": "--- a/Demo.java\n+++ b/Demo.java\n@@\n-class Demo { int x = 1; }\n+class Demo { int x = 2; }",
        }
    )

    assert result.success is True
    assert result.data["lint_passed"] is True
    assert "ok" in result.data["lint_output"]


def test_edit_code_reverts_file_when_lint_fails(tmp_path):
    """lint 失败时应恢复原文件内容并返回失败。"""
    path = tmp_path / "Demo.java"
    original = "class Demo { int x = 1; }\n"
    path.write_text(original, encoding="utf-8")
    config = AppConfig()
    config.project.root = str(tmp_path)
    config.project.lint_command = "python -c \"import sys; sys.exit(1)\""
    tool = EditCodeTool(config)

    result = tool.run(
        {
            "path": str(path),
            "content": "--- a/Demo.java\n+++ b/Demo.java\n@@\n-class Demo { int x = 1; }\n+class Demo { int x = 2; }",
        }
    )

    assert result.success is False
    assert "lint failed" in result.stderr_summary
    assert result.data["lint_passed"] is False
    assert path.read_text(encoding="utf-8") == original


def test_edit_code_reverts_new_file_when_lint_fails(tmp_path):
    """新建文件 lint 失败时应删除该文件。"""
    path = tmp_path / "New.java"
    config = AppConfig()
    config.project.root = str(tmp_path)
    config.project.lint_command = "python -c \"import sys; sys.exit(1)\""
    tool = EditCodeTool(config)

    result = tool.run(
        {
            "path": str(path),
            "content": "--- /dev/null\n+++ b/New.java\n@@\n+class New {}",
        }
    )

    assert result.success is False
    assert "lint failed" in result.stderr_summary
    assert path.read_text(encoding="utf-8") == ""


def test_edit_code_handles_missing_lint_command(tmp_path):
    """lint 命令不存在时应恢复文件并返回失败。"""
    path = tmp_path / "Demo.java"
    original = "class Demo { int x = 1; }\n"
    path.write_text(original, encoding="utf-8")
    config = AppConfig()
    config.project.root = str(tmp_path)
    config.project.lint_command = "nonexistent_lint_tool_12345"
    tool = EditCodeTool(config)

    result = tool.run(
        {
            "path": str(path),
            "content": "--- a/Demo.java\n+++ b/Demo.java\n@@\n-class Demo { int x = 1; }\n+class Demo { int x = 2; }",
        }
    )

    assert result.success is False
    assert "lint failed" in result.stderr_summary
    assert "not found" in result.data["lint_output"]
    assert path.read_text(encoding="utf-8") == original
