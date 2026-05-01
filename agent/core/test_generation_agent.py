from __future__ import annotations

"""Generate regression tests for successful repair patches."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.config import AppConfig
from agent.llm.openai_compatible_client import OpenAICompatibleClient
from agent.models import BugEvent, ToolResult
from agent.tools.apply_test_patch import ApplyTestPatchTool


@dataclass(slots=True)
class TestGenerationResult:
    success: bool
    skipped: bool
    message: str
    tool_result: ToolResult | None = None
    prompt: str = ""
    patch: dict[str, Any] | None = None


class TestGenerationAgent:
    """Ask the LLM for a focused regression test and apply it safely."""

    def __init__(
        self,
        config: AppConfig,
        llm_client: OpenAICompatibleClient | None,
        apply_tool: ApplyTestPatchTool | None = None,
    ) -> None:
        self.config = config
        self.llm_client = llm_client
        self.apply_tool = apply_tool or ApplyTestPatchTool(config)

    def generate_for_repair(
        self,
        *,
        bug_event: BugEvent,
        session: dict[str, Any],
        edit_result: ToolResult,
        history: list[dict[str, Any]],
    ) -> TestGenerationResult:
        if self.llm_client is None:
            return TestGenerationResult(False, True, "llm client unavailable")

        source_path = self._edited_source_path(edit_result)
        if source_path is None:
            return TestGenerationResult(False, True, "no edited Java source path")

        prompt = self._build_prompt(bug_event=bug_event, session=session, source_path=source_path, history=history)
        result: ToolResult | None = None
        patch: dict[str, str] | None = None
        for attempt in range(2):
            response = self.llm_client.chat(
                [{"role": "system", "content": prompt}],
                response_format={"type": "json_object"},
            )
            if not response.success:
                return TestGenerationResult(False, True, f"llm failed: {response.stderr_summary}", prompt=prompt)

            patch = self._parse_patch(response)
            if patch is None:
                message = "llm did not return a valid test patch"
                if attempt == 0:
                    prompt = self._retry_prompt(prompt, message)
                    continue
                return TestGenerationResult(False, True, message, prompt=prompt)

            result = self.apply_tool.run(patch)
            if result.success:
                break
            if attempt == 0 and self._should_retry(result):
                prompt = self._retry_prompt(prompt, result.stderr_summary)
                continue
            break

        if result is None:
            return TestGenerationResult(False, True, "test generation did not produce a patch", prompt=prompt, patch=patch)
        return TestGenerationResult(
            success=result.success,
            skipped=False,
            message="test patch applied" if result.success else result.stderr_summary,
            tool_result=result,
            prompt=prompt,
            patch=patch,
        )

    def _build_prompt(
        self,
        *,
        bug_event: BugEvent,
        session: dict[str, Any],
        source_path: Path,
        history: list[dict[str, Any]],
    ) -> str:
        source_code = self._read_text(source_path, limit=12000)
        test_candidates = self._test_candidates(source_path)
        existing_tests = [
            {"path": str(path), "content": self._read_text(path, limit=8000)}
            for path in test_candidates[:3]
        ]
        last_edit = next((item for item in reversed(history) if item.get("tool") == "edit_code"), {})
        context = {
            "role": "Generate a focused Java regression test for an automated repair.",
            "rules": [
                "Return only a JSON object with fields path and content.",
                "content must be a unified diff for exactly one Java test file.",
                "Do not return a full Java file, raw class body, markdown fence, or prose.",
                "For a new test file, content must start with --- /dev/null and +++ b/src/test/java/...Test.java.",
                "Every added line in the unified diff must start with '+', except diff headers and @@ hunks.",
                "Only create or edit files under src/test/java.",
                "The target file name must end with Test.java.",
                "Do not modify production code, build files, configs, or CI files.",
                "The test must include a meaningful assertion or verification.",
                "Do not use @Disabled, @Ignore, assertTrue(true), or empty smoke tests.",
                "Prefer a regression test that would fail before the repair and pass after it.",
            ],
            "bug_event": bug_event.model_dump(mode="json"),
            "edited_source_path": str(source_path),
            "edited_source_code": source_code,
            "existing_tests": existing_tests,
            "last_edit_result": last_edit.get("result", {}),
            "frame_contexts": session.get("frame_contexts", []),
            "output_example": {
                "path": "src/test/java/com/example/DemoServiceTest.java",
                "content": "--- /dev/null\n+++ b/src/test/java/com/example/DemoServiceTest.java\n@@\n+class DemoServiceTest {\n+    void verifiesRegression() {\n+        assert true;\n+    }\n+}",
            },
        }
        return json.dumps(context, ensure_ascii=False, default=str)

    def _retry_prompt(self, prompt: str, error: str) -> str:
        retry_context = {
            "previous_request": prompt,
            "tool_rejection": error,
            "retry_rules": [
                "Return the same JSON shape: path and content.",
                "Fix only the rejected test patch format or safety issue.",
                "content must be a real unified diff, not a complete file or snippet.",
                "Keep the test focused and under the patch size limit.",
            ],
        }
        return json.dumps(retry_context, ensure_ascii=False, default=str)

    def _should_retry(self, result: ToolResult) -> bool:
        retryable = (
            "requires a unified diff",
            "exactly one diff header pair",
            "diff header path does not match",
            "patch adds too many lines",
            "patch has too many hunks",
            "generated test must include",
        )
        return any(marker in result.stderr_summary for marker in retryable)

    def _parse_patch(self, response: ToolResult) -> dict[str, str] | None:
        data = response.data if isinstance(response.data, dict) else {}
        raw = str(data.get("content") or response.stdout_summary or "").strip()
        if not raw:
            return None
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:].strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        path = str(payload.get("path", "")).strip()
        content = str(payload.get("content", "")).strip()
        if not path or not content:
            return None
        return {"path": path, "content": content}

    def _edited_source_path(self, edit_result: ToolResult) -> Path | None:
        raw_paths = list(edit_result.artifacts or [])
        raw_path = edit_result.data.get("path") if isinstance(edit_result.data, dict) else None
        if raw_path:
            raw_paths.append(str(raw_path))
        for raw in raw_paths:
            path = Path(str(raw))
            if not path.is_absolute():
                path = Path(self.config.project.root) / path
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved.suffix.lower() == ".java" and resolved.exists():
                return resolved
        return None

    def _test_candidates(self, source_path: Path) -> list[Path]:
        try:
            relative = source_path.resolve().relative_to(Path(self.config.project.root).resolve())
        except ValueError:
            return []
        parts = list(relative.parts)
        try:
            src_index = parts.index("src")
        except ValueError:
            return []
        if parts[src_index : src_index + 3] != ["src", "main", "java"]:
            return []

        test_relative = Path(*parts[:src_index], "src", "test", "java", *parts[src_index + 3 :])
        expected = Path(self.config.project.root) / test_relative
        expected = expected.with_name(f"{expected.stem}Test.java")
        candidates = [expected]
        if expected.parent.exists():
            candidates.extend(path for path in expected.parent.glob("*Test.java") if path != expected)
        return candidates

    def _read_text(self, path: Path, *, limit: int) -> str:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return ""
        return text[:limit]
