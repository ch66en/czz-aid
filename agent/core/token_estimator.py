from __future__ import annotations

"""为上下文压缩提供轻量、无外部依赖的 token 估算。"""

import json
from typing import Any


class TokenEstimator:
    """粗略估算 OpenAI-compatible 聊天请求占用的 token 数。"""

    # role、name、tool_call_id 等协议字段会带来固定开销。这里故意略微高估，
    # 让 compact 比模型硬限制更早触发，降低供应商 tokenizer 差异带来的风险。
    MESSAGE_OVERHEAD_TOKENS = 8

    def estimate_text(self, text: str) -> int:
        """按 UTF-8 字节估算 token，兼顾中文和英文输入。"""
        if not text:
            return 0
        return max(1, (len(text.encode("utf-8")) + 3) // 4)

    def estimate_value(self, value: Any) -> int:
        """将结构化值序列化后估算，避免遗漏 tool_calls 等嵌套字段。"""
        serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        return self.estimate_text(serialized)

    def estimate_messages(self, messages: list[dict[str, Any]]) -> int:
        """估算全部聊天消息。"""
        return sum(self.MESSAGE_OVERHEAD_TOKENS + self.estimate_value(message) for message in messages)

    def estimate_tools(self, tools: list[dict[str, Any]] | None) -> int:
        """估算工具 schema；主模型每轮都需要发送这些定义。"""
        if not tools:
            return 0
        return self.estimate_value(tools)

    def estimate_context(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> int:
        """估算一次完整聊天请求的输入 token。"""
        return self.estimate_messages(messages) + self.estimate_tools(tools)
