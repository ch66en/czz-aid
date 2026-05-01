import json

from agent.config import AppConfig
from agent.core.test_generation_agent import TestGenerationAgent
from agent.models import BugEvent, ToolResult
from agent.tools.apply_test_patch import ApplyTestPatchTool


class FakeTestLLM:
    def __init__(self, payload):
        self.payload = payload
        self.last_messages = None
        self.last_response_format = None

    def chat(self, messages, response_format=None, **_kwargs):
        self.last_messages = messages
        self.last_response_format = response_format
        return ToolResult(
            tool="llm_chat",
            success=True,
            exit_code=0,
            data={"content": json.dumps(self.payload)},
        )


class SequenceTestLLM:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = 0

    def chat(self, messages, response_format=None, **_kwargs):
        payload = self.payloads[self.calls]
        self.calls += 1
        return ToolResult(
            tool="llm_chat",
            success=True,
            exit_code=0,
            data={"content": json.dumps(payload)},
        )


def test_test_generation_agent_applies_llm_test_patch(tmp_path):
    root = tmp_path / "project"
    source = root / "src" / "main" / "java" / "DemoService.java"
    source.parent.mkdir(parents=True)
    source.write_text("class DemoService { String name() { return \"ok\"; } }\n", encoding="utf-8")
    test_path = root / "src" / "test" / "java" / "DemoServiceTest.java"
    config = AppConfig()
    config.project.root = str(root)
    llm = FakeTestLLM(
        {
            "path": str(test_path),
            "content": "--- /dev/null\n"
            "+++ b/src/test/java/DemoServiceTest.java\n"
            "@@\n"
            "+class DemoServiceTest {\n"
            "+    void verifiesRepair() {\n"
            "+        assert new DemoService().name().equals(\"ok\");\n"
            "+    }\n"
            "+}",
        }
    )
    agent = TestGenerationAgent(config, llm, ApplyTestPatchTool(config))
    edit_result = ToolResult(tool="edit_code", success=True, exit_code=0, data={"path": str(source)}, artifacts=[str(source)])
    bug = BugEvent(bug_id="BUG-T", source="log", project="demo", title="NPE", exception_type="NullPointerException", message="", fingerprint="fp")

    result = agent.generate_for_repair(bug_event=bug, session={}, edit_result=edit_result, history=[])

    assert result.success is True
    assert test_path.exists()
    assert llm.last_response_format == {"type": "json_object"}


def test_test_generation_agent_retries_when_llm_returns_full_file(tmp_path):
    root = tmp_path / "project"
    source = root / "src" / "main" / "java" / "DemoService.java"
    source.parent.mkdir(parents=True)
    source.write_text("class DemoService { String name() { return \"ok\"; } }\n", encoding="utf-8")
    test_path = root / "src" / "test" / "java" / "DemoServiceTest.java"
    config = AppConfig()
    config.project.root = str(root)
    llm = SequenceTestLLM(
        [
            {
                "path": str(test_path),
                "content": "class DemoServiceTest { void verifiesRepair() { assert new DemoService().name().equals(\"ok\"); } }",
            },
            {
                "path": str(test_path),
                "content": "--- /dev/null\n"
                "+++ b/src/test/java/DemoServiceTest.java\n"
                "@@\n"
                "+class DemoServiceTest {\n"
                "+    void verifiesRepair() {\n"
                "+        assert new DemoService().name().equals(\"ok\");\n"
                "+    }\n"
                "+}",
            },
        ]
    )
    agent = TestGenerationAgent(config, llm, ApplyTestPatchTool(config))
    edit_result = ToolResult(tool="edit_code", success=True, exit_code=0, data={"path": str(source)}, artifacts=[str(source)])
    bug = BugEvent(bug_id="BUG-T", source="log", project="demo", title="NPE", exception_type="NullPointerException", message="", fingerprint="fp")

    result = agent.generate_for_repair(bug_event=bug, session={}, edit_result=edit_result, history=[])

    assert result.success is True
    assert llm.calls == 2
    assert test_path.exists()


def test_test_generation_agent_skips_without_llm(tmp_path):
    config = AppConfig()
    config.project.root = str(tmp_path)
    agent = TestGenerationAgent(config, None, ApplyTestPatchTool(config))
    edit_result = ToolResult(tool="edit_code", success=True, exit_code=0, data={}, artifacts=[])
    bug = BugEvent(bug_id="BUG-T", source="log", project="demo", title="", exception_type="E", message="", fingerprint="fp")

    result = agent.generate_for_repair(bug_event=bug, session={}, edit_result=edit_result, history=[])

    assert result.skipped is True
    assert "llm client unavailable" in result.message
