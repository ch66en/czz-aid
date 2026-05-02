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
        self.fallback_client: OpenAI | None = None
        if self.router.has_fallback():
            self.fallback_client = OpenAI(
                api_key=config.llm.fallback_api_key,
                base_url=self.router.choose_fallback_base_url(),
            )

    def ping(self) -> None:
        """主动验证 LLM 连接可用性，失败则尝试备用 LLM，都失败则抛错。"""
        model = self.router.choose_model()
        try:
            self.client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
            return
        except Exception as primary_exc:
            if self.fallback_client is None:
                raise RuntimeError(f"LLM connection failed: {primary_exc}") from primary_exc
            fallback_model = self.router.choose_fallback_model()
            try:
                self.fallback_client.chat.completions.create(
                    model=fallback_model,
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=1,
                )
                return
            except Exception as fallback_exc:
                raise RuntimeError(
                    f"LLM connection failed: primary={primary_exc}, fallback={fallback_exc}"
                ) from fallback_exc

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> ToolResult:
        """向兼容接口模型发起聊天请求，主 LLM 失败时自动尝试备用 LLM。"""
        start = time.perf_counter()
        model = self.router.choose_model()
        request_kwargs = self._build_request_kwargs(model, messages, tools, response_format, tool_choice)
        try:
            response = self.client.chat.completions.create(**request_kwargs)
        except Exception as primary_exc:
            if self.fallback_client is None:
                return self._failure_result(model, messages, tools, start, primary_exc)
            fallback_model = self.router.choose_fallback_model()
            fallback_kwargs = self._build_request_kwargs(fallback_model, messages, tools, response_format, tool_choice)
            try:
                response = self.fallback_client.chat.completions.create(**fallback_kwargs)
            except Exception as fallback_exc:
                return self._failure_result(fallback_model, messages, tools, start, fallback_exc)
            return self._success_result(fallback_model, messages, tools, response, start, fallback_used=True)
        return self._success_result(model, messages, tools, response, start, fallback_used=False)

    def _build_request_kwargs(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        response_format: dict[str, Any] | None,
        tool_choice: str | dict[str, Any] | None,
    ) -> dict[str, Any]:
        """构建 OpenAI API 请求参数。"""
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "tools": tools or None,
            "timeout": self.config.llm.timeout_seconds,
        }
        if response_format is not None:
            kwargs["response_format"] = response_format
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        return kwargs

    def _failure_result(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        start: float,
        exc: Exception,
    ) -> ToolResult:
        """构造调用失败的 ToolResult 并记录。"""
        latency_ms = int((time.perf_counter() - start) * 1000)
        summary = f"model={model}, messages={len(messages)}, tools={len(tools or [])}, latency_ms={latency_ms}"
        record = LLMCallRecord(summary=summary, latency_ms=latency_ms)
        self.records.append(record)
        return ToolResult(
            tool="llm_chat",
            success=False,
            exit_code=1,
            stdout_summary="",
            stderr_summary=str(exc),
            data={"model": model, "summary": summary},
            artifacts=[],
        )

    def _success_result(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        response: Any,
        start: float,
        fallback_used: bool,
    ) -> ToolResult:
        """构造调用成功的 ToolResult 并持久化日志。"""
        latency_ms = int((time.perf_counter() - start) * 1000)
        choice = response.choices[0]
        message = choice.message
        content = getattr(message, "content", "") or ""
        tool_calls = self._extract_tool_calls(message)
        summary = f"model={model}, messages={len(messages)}, tools={len(tools or [])}"
        token_usage = getattr(response, "usage", None).model_dump() if getattr(response, "usage", None) else None
        if self.config.agent.debug:
            raw_input = self.sanitizer.sanitize(json.dumps(messages, ensure_ascii=False, indent=2, default=str))
            raw_output = self.sanitizer.sanitize(json.dumps({"content": content, "tool_calls": tool_calls}, ensure_ascii=False, default=str))
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
        artifact_path = self._persist_call_record(model=model, messages=messages, tools=tools, content=content, tool_calls=tool_calls, token_usage=token_usage, latency_ms=latency_ms)
        self.records.append(record)
        data: dict[str, Any] = {"model": model, "summary": record.summary, "content": content, "tool_calls": tool_calls, "artifact_path": str(artifact_path)}
        if fallback_used:
            data["fallback_used"] = True
        return ToolResult(
            tool="llm_chat",
            success=True,
            exit_code=0,
            stdout_summary=content[:2000],
            stderr_summary="",
            data=data,
            artifacts=[str(artifact_path)],
        )

    def _persist_call_record(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        content: str,
        tool_calls: list[dict[str, Any]],
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
            "tool_calls": tool_calls,
            "token_usage": token_usage,
        }
        sanitized_payload = self._sanitize_json_value(payload)
        path.write_text(json.dumps(sanitized_payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return path

    def _extract_tool_calls(self, message: Any) -> list[dict[str, Any]]:
        """Extract OpenAI-compatible tool call data from a chat message."""
        calls = getattr(message, "tool_calls", None) or []
        result: list[dict[str, Any]] = []
        for call in calls:
            function = getattr(call, "function", None)
            name = getattr(function, "name", "") if function is not None else ""
            arguments = getattr(function, "arguments", "{}") if function is not None else "{}"
            call_type = getattr(call, "type", "function") or "function"
            call_id = getattr(call, "id", "")
            if hasattr(call, "model_dump"):
                raw = call.model_dump()
                call_id = str(raw.get("id") or call_id)
                call_type = str(raw.get("type") or call_type)
                raw_function = raw.get("function") if isinstance(raw.get("function"), dict) else {}
                name = str(raw_function.get("name") or name)
                arguments = raw_function.get("arguments", arguments)
            result.append(
                {
                    "id": str(call_id),
                    "type": str(call_type),
                    "function": {
                        "name": str(name),
                        "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False, default=str),
                    },
                }
            )
        return result

    def _sanitize_json_value(self, value: Any) -> Any:
        """Sanitize string leaves without corrupting JSON structure."""
        if isinstance(value, str):
            return self.sanitizer.sanitize(value)
        if isinstance(value, list):
            return [self._sanitize_json_value(item) for item in value]
        if isinstance(value, dict):
            return {str(key): self._sanitize_json_value(item) for key, item in value.items()}
        return value
