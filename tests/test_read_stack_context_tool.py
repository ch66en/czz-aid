from __future__ import annotations

from pathlib import Path

from agent.config import AppConfig
from agent.tools.read_stack_context import ReadStackContextTool


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "java-demo"


def test_read_stack_context_resolves_traceback_to_symbol() -> None:
    tool = ReadStackContextTool(AppConfig(project={"root": str(FIXTURE_ROOT)}))
    traceback = """
    java.lang.NullPointerException: boom
        at com.demo.book.BookService.getTitle(BookService.java:11)
        at org.springframework.web.servlet.DispatcherServlet.doDispatch(DispatcherServlet.java:1)
    """.strip()

    result = tool.run({"traceback": traceback, "package_prefix": "com.demo"})

    assert result.success is True
    assert len(result.data["contexts"]) == 1
    context = result.data["contexts"][0]
    assert context["functionName"] == "getTitle"
    assert context["symbol"]["name"] == "getTitle"
    assert any("return book.getTitle()" in line["text"] for line in context["code"])
    assert result.artifacts


def test_read_stack_context_falls_back_to_method_name_when_line_mismatches() -> None:
    tool = ReadStackContextTool(AppConfig(project={"root": str(FIXTURE_ROOT)}))

    result = tool.run(
        {
            "frames": [
                {
                    "filePath": "BookService.java",
                    "functionName": "detail",
                    "lineNumber": 88,
                    "moduleName": "com.demo.book.BookService",
                }
            ],
            "package_prefix": "com.demo",
        }
    )

    assert result.success is True
    context = result.data["contexts"][0]
    assert context["functionName"] == "detail"
    assert context["symbol"]["name"] == "detail"
    assert any("return bookRepository.findById(id)" in line["text"] for line in context["code"])
