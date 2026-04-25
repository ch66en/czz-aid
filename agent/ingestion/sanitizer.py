from __future__ import annotations

"""提供日志与文本中的敏感信息脱敏能力。"""

import re


class Sanitizer:
    """负责对日志文本进行统一脱敏处理。"""

    _EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    _PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
    _AUTH_PATTERN = re.compile(r"(Authorization\s*:\s*Bearer\s+)([A-Za-z0-9\-._~+/=]+)", re.IGNORECASE)
    _COOKIE_PATTERN = re.compile(r"(Cookie\s*:\s*)(.+)", re.IGNORECASE)
    _SESSION_ID_PATTERN = re.compile(r"\b(session(?:id)?|jsessionid|phpsessid)\b\s*[:=]\s*([A-Za-z0-9\-._~+/=]+)", re.IGNORECASE)
    _PASSWORD_PATTERN = re.compile(r"\b(password|passwd|pwd)\b\s*[:=]\s*([^\s,&;]+)", re.IGNORECASE)
    _ACCESS_KEY_PATTERN = re.compile(r"\b(access_key|secret_key|accesskey|secretkey)\b\s*[:=]\s*([^\s,&;]+)", re.IGNORECASE)
    _JDBC_URL_PATTERN = re.compile(r"(jdbc:[^\s]+)", re.IGNORECASE)

    def sanitize(self, text: str) -> str:
        """对输入文本执行脱敏，返回完整的脱敏后文本。"""
        sanitized = text
        sanitized = self._AUTH_PATTERN.sub(r"\1[REDACTED]", sanitized)
        sanitized = self._COOKIE_PATTERN.sub(lambda m: f"{m.group(1)}[REDACTED]", sanitized)
        sanitized = self._SESSION_ID_PATTERN.sub(lambda m: f"{m.group(1)}=[REDACTED]", sanitized)
        sanitized = self._PASSWORD_PATTERN.sub(lambda m: f"{m.group(1)}=[REDACTED]", sanitized)
        sanitized = self._ACCESS_KEY_PATTERN.sub(lambda m: f"{m.group(1)}=[REDACTED]", sanitized)
        sanitized = self._mask_jdbc_urls(sanitized)
        sanitized = re.sub(r"(?i)\b(token|secret|signature|api[_-]?key|access[_-]?token)\b\s*[:=]\s*([^\s,&;]+)", lambda m: f"{m.group(1)}=[REDACTED]", sanitized)
        sanitized = self._EMAIL_PATTERN.sub("[EMAIL]", sanitized)
        sanitized = self._PHONE_PATTERN.sub("[PHONE]", sanitized)
        return sanitized

    def _mask_jdbc_urls(self, text: str) -> str:
        """脱敏 JDBC URL 中的用户名和密码参数。"""
        def replace(match: re.Match[str]) -> str:
            url = match.group(1)
            url = re.sub(r"(?i)([?&](?:user|username))=([^&\s]+)", r"\1=[REDACTED]", url)
            url = re.sub(r"(?i)([?&](?:password|pass))=([^&\s]+)", r"\1=[REDACTED]", url)
            return url

        return self._JDBC_URL_PATTERN.sub(replace, text)
