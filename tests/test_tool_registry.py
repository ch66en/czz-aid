"""验证工具注册表的基础行为。"""

from agent.core.tool_registry import ToolRegistry
from agent.tools.git_tool import GitTool


def test_tool_registry_registers_tool() -> None:
    """注册后的工具应可按名称读取并列出规格。"""
    registry = ToolRegistry()
    tool = GitTool()
    registry.register(tool)
    assert registry.get("git_tool") is tool
    assert registry.list_specs()[0].name == "git_tool"
