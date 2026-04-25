"""验证脱敏组件的基础行为。"""

from agent.ingestion.sanitizer import Sanitizer


def test_sanitizer_masks_sensitive_values() -> None:
    """常见敏感字段应被统一脱敏。"""
    sanitizer = Sanitizer()
    text = """
    Authorization: Bearer abc.def.ghi
    Cookie: sessionid=abc123; token=xyz
    sessionId=123456
    手机号 13800138000
    邮箱 test@example.com
    password=secret123
    access_key=AKIA123456
    secret_key=SK123456
    jdbc:mysql://localhost:3306/demo?user=root&password=123456
    """.strip()

    result = sanitizer.sanitize(text)

    assert "abc.def.ghi" not in result
    assert "sessionid=abc123" not in result
    assert "123456" not in result
    assert "13800138000" not in result
    assert "test@example.com" not in result
    assert "secret123" not in result
    assert "AKIA123456" not in result
    assert "SK123456" not in result
    assert "root" not in result
    assert "123456" not in result
    assert "[REDACTED]" in result
    assert "[EMAIL]" in result
    assert "[PHONE]" in result
