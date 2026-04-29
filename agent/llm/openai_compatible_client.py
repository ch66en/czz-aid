from __future__ import annotations

"""提供兼容 OpenAI 接口风格的客户端封装。"""

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from openai import OpenAI

from agent.config import AppConfig
from agent.ingestion.sanitizer import Sanitizer
from agent.llm.model_router import ModelRouter
from agent.models import ToolResult


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

    def ping(self) -> None:
        """主动验证 LLM 连接可用性，失败则直接抛错。"""
        model = self.router.choose_model()
        try:
            self.client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
        except Exception as exc:
            raise RuntimeError(f"LLM connection failed: {exc}") from exc

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> ToolResult:
        """向兼容接口模型发起聊天请求并记录摘要。"""
        start = time.perf_counter()
        model = self.router.choose_model()
        response = self.client.chat.completions.create(model=model, messages=messages, tools=tools or None)
        latency_ms = int((time.perf_counter() - start) * 1000)
        choice = response.choices[0]
        content = getattr(choice.message, "content", "") or ""
        summary = f"model={model}, messages={len(messages)}, tools={len(tools or [])}"
        token_usage = getattr(response, "usage", None).model_dump() if getattr(response, "usage", None) else None
        if self.config.agent.debug:
            raw_input = self.sanitizer.sanitize(json.dumps(messages, ensure_ascii=False, indent=2, default=str))
            raw_output = self.sanitizer.sanitize(content)
        else:
            raw_input = ""
            raw_output = ""
        record = LLMCallRecord(
            summary=f"{summary}, latency_ms={latency_ms}",
            input_text=raw_input,
            output_text=raw_output,
            token_usage=token_usage,
            latency_ms=latency_ms,
        )
        artifact_path = self._persist_call_record(model=model, messages=messages, tools=tools, content=content, token_usage=token_usage, latency_ms=latency_ms)
        self.records.append(record)
        return ToolResult(
            tool="llm_chat",
            success=True,
            exit_code=0,
            stdout_summary=content[:2000],
            stderr_summary="",
            data={"model": model, "summary": record.summary, "content": content, "artifact_path": str(artifact_path)},
            artifacts=[str(artifact_path)],
        )

    def _persist_call_record(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        content: str,
        token_usage: dict[str, Any] | None,
        latency_ms: int,
    ) -> Path:
        """将一次 LLM 调用的完整输入输出持久化到文件。"""
        log_dir = Path(self.config.session.root_dir) / "llm_calls"
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = log_dir / f"llm-{timestamp}.json"
        payload = {
            "timestamp": timestamp,
            "model": model,
            "latency_ms": latency_ms,
            "messages": messages,
            "tools": tools or [],
            "output": content,
            "token_usage": token_usage,
        }
        sanitized_text = self.sanitizer.sanitize(json.dumps(payload, ensure_ascii=False, default=str))
        sanitized_payload = json.loads(sanitized_text)
        path.write_text(json.dumps(sanitized_payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return path
