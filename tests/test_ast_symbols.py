"""验证 Java AST 符号提取与按行定位。"""

from pathlib import Path

from agent.config import AppConfig
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


def test_ast_symbols_tracks_container_parents_for_java_edge_cases(tmp_path: Path) -> None:
    source = tmp_path / "EdgeCases.java"
    source.write_text(
        "\n".join(
            [
                "package demo;",
                "",
                "interface Ops {",
                "    default String label() {",
                "        return \"ok\";",
                "    }",
                "}",
                "",
                "public class EdgeCases {",
                "    public EdgeCases() {",
                "    }",
                "",
                "    class Inner {",
                "        String value() {",
                "            return \"inner\";",
                "        }",
                "    }",
                "",
                "    Runnable runnable() {",
                "        return new Runnable() {",
                "            @Override",
                "            public void run() {",
                "                System.out.println(\"x\");",
                "            }",
                "        };",
                "    }",
                "}",
                "",
                "record EdgeRecord(String name) {",
                "    String upper() {",
                "        return name.toUpperCase();",
                "    }",
                "}",
            ]
        ),
        encoding="utf-8",
    )
    extractor = JavaAstSymbolExtractor()

    result = extractor.extract(str(source))
    symbols = result["symbols"]

    constructor = next(symbol for symbol in symbols if symbol["kind"] == "constructor" and symbol["name"] == "EdgeCases")
    inner = next(symbol for symbol in symbols if symbol["kind"] == "class" and symbol["name"] == "Inner")
    inner_value = next(symbol for symbol in symbols if symbol["kind"] == "method" and symbol["name"] == "value")
    default_method = next(symbol for symbol in symbols if symbol["kind"] == "method" and symbol["name"] == "label")
    record_method = next(symbol for symbol in symbols if symbol["kind"] == "method" and symbol["name"] == "upper")

    assert constructor["parent"] == "EdgeCases"
    assert inner["parent"] == "EdgeCases"
    assert inner_value["parent"] == "Inner"
    assert default_method["parent"] == "Ops"
    assert record_method["parent"] == "EdgeRecord"

    anonymous_line = source.read_text(encoding="utf-8").splitlines().index('                System.out.println("x");') + 1
    symbol_at = extractor.find_symbol_at(str(source), anonymous_line)

    assert symbol_at["symbol"]["name"] == "run"


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


def test_read_code_resolves_relative_path_from_project_root(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    source = project_root / "src" / "main" / "java" / "Demo.java"
    source.parent.mkdir(parents=True)
    source.write_text("class Demo {}\n", encoding="utf-8")
    config = AppConfig()
    config.project.root = str(project_root)
    tool = ReadCodeTool(config)

    result = tool.run({"path": "src/main/java/Demo.java"})

    assert result.success is True
    assert result.data["path"] == str(source)
    assert "class Demo" in result.data["content"]
