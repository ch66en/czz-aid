from __future__ import annotations

"""Safe test patch application tool."""

from pathlib import Path
from typing import Any

from agent.config import AppConfig
from agent.models import ToolSpec
from agent.tools.base import PermissionType
from agent.tools.edit_code import EditCodeTool


class ApplyTestPatchTool(EditCodeTool):
    """Apply a unified diff that is restricted to Java test sources."""

    TOOL_NAME = "apply_test_patch"
    MAX_HUNKS = 5
    MAX_ADDED_LINES = 150
    MAX_DELETED_LINES = 20
    WEAK_TEST_PATTERNS = (
        "@Disabled",
        "@Ignore",
        "assertTrue(true)",
        "Assertions.assertTrue(true)",
        "// assert",
    )

    def __init__(self, config: AppConfig | None = None) -> None:
        super().__init__(config)

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.TOOL_NAME,
            description=(
                "Apply a single-file unified diff for Java regression tests. "
                "Only src/test/java/**/*Test.java targets are allowed. "
                "New test files are allowed; production files and weak tests are rejected."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or project-relative path under src/test/java."},
                    "content": {"type": "string", "description": "Unified diff text for exactly one Java test file."},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            permission=PermissionType.WORKSPACE_WRITE.value,
            executor="local",
        )

    def _validate_patch_request(self, path: Path, content: str) -> str:
        if self.config is None:
            return "apply_test_patch requires project config"

        project_root = Path(self.config.project.root).expanduser().resolve()
        target = path.expanduser().resolve()
        try:
            relative = target.relative_to(project_root)
        except ValueError:
            return "path is outside project root"

        if target.suffix.lower() != ".java":
            return "apply_test_patch only allows Java test files"
        if not target.name.endswith("Test.java"):
            return "test patch target must end with Test.java"
        if self._is_forbidden_path(relative):
            return "path is forbidden for automated test generation"
        if not self._is_under_test_source_root(relative):
            return "path is not in test source roots"
        if path.exists() and not path.is_file():
            return "path is not a regular file"

        header_error = self._validate_test_diff_headers_match_target(relative, content)
        if header_error:
            return header_error
        weak_test_error = self._validate_test_content(content)
        if weak_test_error:
            return weak_test_error
        return ""

    def _is_under_test_source_root(self, relative: Path) -> bool:
        parts = tuple(part.lower() for part in relative.parts)
        for index in range(len(parts) - 2):
            if parts[index : index + 3] == ("src", "test", "java"):
                return True
        return False

    def _validate_test_diff_headers_match_target(self, relative: Path, content: str) -> str:
        old_path, new_path, error = self._extract_single_diff_header(content)
        if error:
            return error
        if new_path == "/dev/null":
            return "test file deletions are not allowed"
        if old_path != "/dev/null" and old_path != new_path:
            return "diff old and new paths must match"
        expected = relative.as_posix()
        if new_path != expected:
            return "diff header path does not match target path"
        return ""

    def _validate_test_content(self, content: str) -> str:
        additions = [line[1:].strip() for line in content.splitlines() if line.startswith("+") and not line.startswith("+++")]
        added_text = "\n".join(additions)
        for pattern in self.WEAK_TEST_PATTERNS:
            if pattern in added_text:
                return f"weak or disabled test pattern rejected: {pattern}"
        if "void " in added_text and "assert" not in added_text and "verify(" not in added_text:
            return "generated test must include an assertion or verification"
        return ""
