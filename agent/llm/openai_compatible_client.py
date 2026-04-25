from __future__ import annotations

"""提供兼容 OpenAI 接口风格的客户端封装。"""

import time
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from agent.config import AppConfig
from agent.llm.model_router import ModelRouter
from agent.models import ToolResult
from agent.ingestion.sanitizer import Sanitizer


@dataclass(slots=True)
class LLMCallRecord:
    """记录一次 LLM 调用的摘要或调试信息。"""

    summary: str
    input_text: str = ""
    output_text: str = ""
    token_usage: dict[str, Any] | None = None
    latency_ms: int | None = None


class OpenAICompatibleClient:
    """封装 OpenAI-compatible LLM 调用与脱敏日志记录。"""

    def __init__(self, config: AppConfig, client: OpenAI | None = None, sanitizer: Sanitizer | None = None) -> None:
        """初始化客户端配置。"""
        self.config = config
        self.router = ModelRouter(config)
        self.client = client or OpenAI(api_key=config.llm.api_key, base_url=self.router.choose_base_url())
        self.sanitizer = sanitizer or Sanitizer()
        self.records: list[LLMCallRecord] = []

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> ToolResult:
        """向兼容接口模型发起聊天请求并记录摘要。"""
        start = time.perf_counter()
        model = self.router.choose_model()
        response = self.client.chat.completions.create(model=model, messages=messages, tools=tools or None)
        latency_ms = int((time.perf_counter() - start) * 1000)
        choice = response.choices[0]
        content = getattr(choice.message, "content", "") or ""
        summary = f"model={model}, messages={len(messages)}, tools={len(tools or [])}"
        record = LLMCallRecord(summary=summary)
        if self.config.agent.debug:
            raw_input = self.sanitizer.sanitize(str(messages))
            raw_output = self.sanitizer.sanitize(content)
            record.input_text = raw_input
            record.output_text = raw_output
            record.token_usage = getattr(response, "usage", None).model_dump() if getattr(response, "usage", None) else None
            record.latency_ms = latency_ms
            record.summary = f"{summary}, latency_ms={latency_ms}"
        self.records.append(record)
        return ToolResult(tool="llm_chat", success=True, exit_code=0, stdout_summary=content[:2000], stderr_summary="", data={"model": model, "summary": record.summary}, artifacts=[])
