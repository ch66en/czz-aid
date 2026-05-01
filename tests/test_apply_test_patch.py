from agent.config import AppConfig
from agent.tools.apply_test_patch import ApplyTestPatchTool


def _tool(tmp_path):
    root = tmp_path / "project"
    config = AppConfig()
    config.project.root = str(root)
    return ApplyTestPatchTool(config), root


def test_apply_test_patch_allows_new_test_file(tmp_path):
    tool, root = _tool(tmp_path)
    target = root / "src" / "test" / "java" / "DemoServiceTest.java"

    result = tool.run(
        {
            "path": str(target),
            "content": "--- /dev/null\n"
            "+++ b/src/test/java/DemoServiceTest.java\n"
            "@@\n"
            "+class DemoServiceTest {\n"
            "+    void repairsNullInput() {\n"
            "+        assert result != null;\n"
            "+    }\n"
            "+}",
        }
    )

    assert result.success is True
    assert target.exists()


def test_apply_test_patch_rejects_production_file(tmp_path):
    tool, root = _tool(tmp_path)
    target = root / "src" / "main" / "java" / "DemoServiceTest.java"
    target.parent.mkdir(parents=True)
    target.write_text("class DemoServiceTest {}\n", encoding="utf-8")

    result = tool.run(
        {
            "path": str(target),
            "content": "--- a/src/main/java/DemoServiceTest.java\n"
            "+++ b/src/main/java/DemoServiceTest.java\n"
            "@@\n"
            "-class DemoServiceTest {}\n"
            "+class DemoServiceTest { void x() { assert true; } }",
        }
    )

    assert result.success is False
    assert "test source roots" in result.stderr_summary


def test_apply_test_patch_rejects_disabled_or_weak_tests(tmp_path):
    tool, root = _tool(tmp_path)
    target = root / "src" / "test" / "java" / "DemoServiceTest.java"

    result = tool.run(
        {
            "path": str(target),
            "content": "--- /dev/null\n"
            "+++ b/src/test/java/DemoServiceTest.java\n"
            "@@\n"
            "+class DemoServiceTest {\n"
            "+    @Disabled\n"
            "+    void disabled() { assertTrue(true); }\n"
            "+}",
        }
    )

    assert result.success is False
    assert "weak or disabled" in result.stderr_summary


def test_apply_test_patch_allows_larger_test_patch_than_source_patch(tmp_path):
    tool, root = _tool(tmp_path)
    target = root / "src" / "test" / "java" / "LargeRegressionTest.java"
    additions = "\n".join(f"+    void helper{i}() {{ assert true; }}" for i in range(80))

    result = tool.run(
        {
            "path": str(target),
            "content": "--- /dev/null\n"
            "+++ b/src/test/java/LargeRegressionTest.java\n"
            "@@\n"
            "+class LargeRegressionTest {\n"
            f"{additions}\n"
            "+}",
        }
    )

    assert result.success is True
    assert target.exists()
