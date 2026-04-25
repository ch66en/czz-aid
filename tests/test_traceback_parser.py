"""验证异常堆栈解析器的基础行为。"""

from agent.ingestion.traceback_parser import TracebackParser


def test_traceback_parser_extracts_last_error_line() -> None:
    """应从最后一行中提取异常类型和错误信息。"""
    parser = TracebackParser()
    parsed = parser.parse("Traceback\nValueError: bad input")
    assert parsed.error_type == "ValueError"
    assert parsed.message == "bad input"
