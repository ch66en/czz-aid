"""验证脱敏组件的基础行为。"""

from agent.ingestion.sanitizer import Sanitizer


def test_sanitizer_masks_email() -> None:
    """邮箱地址应被替换为统一占位符。"""
    sanitizer = Sanitizer()
    result = sanitizer.sanitize("contact me at test@example.com")
    assert "[EMAIL]" in result
