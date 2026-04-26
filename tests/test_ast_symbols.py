"""验证 Java AST 符号提取与按行定位。"""

from pathlib import Path

from agent.code_nav.ast_symbols import JavaAstSymbolExtractor
from agent.tools.read_code import ReadCodeTool


FIXTURE = Path(__file__).parent / "fixtures" / "java-demo" / "src" / "main" / "java" / "com" / "demo" / "book" / "BookService.java"


def test_ast_symbols_extract_java_symbols() -> None:
    extractor = JavaAstSymbolExtractor()

    result = extractor.extract(str(FIXTURE))

    assert result["language"] == "java"
    assert result["hasSyntaxError"] is False
    symbols = result["symbols"]
    assert any(symbol["kind"] == "class" and symbol["name"] == "BookService" for symbol in symbols)
    assert any(symbol["kind"] == "constructor" and symbol["name"] == "BookService" for symbol in symbols)
    get_title = next(symbol for symbol in symbols if symbol["kind"] == "method" and symbol["name"] == "getTitle")
    assert get_title["parent"] == "BookService"
    assert get_title["startLine"] < get_title["endLine"]
    assert "getTitle" in get_title["signature"]


def test_read_symbol_at_java_method() -> None:
    extractor = JavaAstSymbolExtractor()

    result = extractor.find_symbol_at(str(FIXTURE), 11)

    assert result["symbol"]["name"] == "getTitle"
    assert any("return book.getTitle()" in line["text"] for line in result["code"])
    assert result["contentHash"].startswith("sha256:")


def test_read_code_range() -> None:
    tool = ReadCodeTool()

    result = tool.run({"path": str(FIXTURE), "start_line": 9, "end_line": 12})

    assert result.success is True
    assert result.data["startLine"] == 9
    assert result.data["endLine"] == 12
    assert result.data["totalLines"] >= 12
    assert result.data["contentHash"].startswith("sha256:")
    assert "9 |" in result.data["content"]


def test_read_code_large_file_requires_range(tmp_path: Path) -> None:
    file_path = tmp_path / "Large.java"
    lines = ["package demo;", "public class Large {"] + [f"    void m{i}() {{}}" for i in range(301)] + ["}"]
    file_path.write_text("\n".join(lines), encoding="utf-8")
    tool = ReadCodeTool()

    result = tool.run({"path": str(file_path)})

    assert result.success is False
    assert "ast_symbols" in result.stderr_summary or "read_symbol_at" in result.stderr_summary
