"""验证异常堆栈解析器的基础行为。"""

from agent.ingestion.traceback_parser import TracebackParser


def test_traceback_parser_extracts_java_traceback() -> None:
    """应从 Java 异常栈中提取结构化堆栈信息。"""
    parser = TracebackParser()
    text = """
    java.lang.NullPointerException: Cannot invoke "Borrow.getId()" because "borrow" is null
        at com.example.book.service.BorrowService.borrowBook(BorrowService.java:42)
        at com.example.book.controller.BorrowController.borrow(BorrowController.java:27)
        at org.springframework.web.method.support.InvocableHandlerMethod.invoke(InvocableHandlerMethod.java:123)
    """.strip()

    parsed = parser.parse(text, package_prefix="com.example")

    assert parsed.exception_type == "java.lang.NullPointerException"
    assert parsed.message.startswith("Cannot invoke")
    assert len(parsed.frames) == 3
    assert parsed.frames[0].module_name == "com.example.book.service.BorrowService"
    assert parsed.frames[0].line_number == 42
    assert parsed.top_business_frame == "com.example.book.service.BorrowService(BorrowService.java:42)"
    assert "BorrowController.borrow" in parsed.normalized_trace


def test_traceback_parser_falls_back_to_first_non_framework_frame() -> None:
    """没有包名前缀时应跳过常见框架包并选出首个业务栈。"""
    parser = TracebackParser()
    text = """
    java.lang.IllegalStateException: boom
        at java.lang.reflect.Method.invoke(Method.java:1)
        at javax.servlet.http.HttpServlet.service(HttpServlet.java:2)
        at org.springframework.web.servlet.DispatcherServlet.doDispatch(DispatcherServlet.java:3)
        at com.acme.order.OrderService.place(OrderService.java:88)
    """.strip()

    parsed = parser.parse(text)

    assert parsed.top_business_frame == "com.acme.order.OrderService(OrderService.java:88)"
    assert parsed.frames[-1].module_name == "com.acme.order.OrderService"
