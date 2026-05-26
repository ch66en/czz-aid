from pathlib import Path

from agent.tools.search_code import SearchCodeTool


def test_search_code_returns_line_snippet_and_context(tmp_path: Path) -> None:
    source = tmp_path / "src" / "main" / "java" / "demo" / "OrderService.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "\n".join(
            [
                "package demo;",
                "",
                "class OrderService {",
                "    void create(Order order) {",
                "        validate(order);",
                "        throw new IllegalArgumentException(\"amount must > 0\");",
                "    }",
                "}",
            ]
        ),
        encoding="utf-8",
    )
    tool = SearchCodeTool()

    result = tool.run({"root": str(tmp_path), "keyword": "amount must > 0", "context_lines": 1})

    assert result.success is True
    assert result.artifacts == [str(source)]
    assert result.data["total"] == 1
    assert result.data["truncated"] is False
    assert result.data["matches"] == [
        {
            "path": str(source),
            "line": 6,
            "snippet": '        throw new IllegalArgumentException("amount must > 0");',
            "before": ["        validate(order);"],
            "after": ["    }"],
            "match_type": "content",
        }
    ]


def test_search_code_supports_regex_file_name_and_max_results(tmp_path: Path) -> None:
    first = tmp_path / "src" / "main" / "java" / "demo" / "OrderService.java"
    second = tmp_path / "src" / "main" / "java" / "demo" / "OrderController.java"
    first.parent.mkdir(parents=True)
    first.write_text("class OrderService {\n    void createOrder() {}\n}\n", encoding="utf-8")
    second.write_text("class OrderController {\n    void createOrder() {}\n}\n", encoding="utf-8")
    tool = SearchCodeTool()

    result = tool.run({"root": str(tmp_path), "keyword": "Order.*\\.java", "regex": True, "max_results": 1})

    assert result.success is True
    assert result.data["total"] == 2
    assert result.data["truncated"] is True
    assert len(result.data["matches"]) == 1
    assert result.data["matches"][0]["line"] == 0
    assert result.data["matches"][0]["match_type"] == "file_name"


def test_search_code_ignores_build_output_and_reports_invalid_regex(tmp_path: Path) -> None:
    source = tmp_path / "src" / "main" / "java" / "demo" / "Demo.java"
    ignored = tmp_path / "target" / "generated-sources" / "Bad.java"
    source.parent.mkdir(parents=True)
    ignored.parent.mkdir(parents=True)
    source.write_text("class Demo {\n    String value = \"needle\";\n}\n", encoding="utf-8")
    ignored.write_text("class Bad {\n    String value = \"needle\";\n}\n", encoding="utf-8")
    tool = SearchCodeTool()

    result = tool.run({"root": str(tmp_path), "keyword": "needle"})
    invalid = tool.run({"root": str(tmp_path), "keyword": "[", "regex": True})

    assert result.success is True
    assert result.data["total"] == 1
    assert result.data["matches"][0]["path"] == str(source)
    assert invalid.success is False
    assert "invalid regex" in invalid.stderr_summary
