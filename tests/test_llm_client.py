"""验证 OpenAI-compatible LLM 客户端封装。"""

from types import SimpleNamespace

from agent.config import AppConfig
from agent.llm.model_router import ModelRouter
from agent.llm.openai_compatible_client import OpenAICompatibleClient


class FakeCompletions:
    """模拟 chat.completions 接口。"""

    def __init__(self, response: object) -> None:
        self.response = response
        self.last_kwargs = None

    def create(self, **kwargs: object) -> object:
        self.last_kwargs = kwargs
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class FakeClient:
    """模拟 OpenAI 客户端。"""

    def __init__(self, response: object) -> None:
        self.chat = SimpleNamespace(completions=FakeCompletions(response))


def test_model_router_switches_provider() -> None:
    """模型路由器应支持不同 provider。"""
    config = AppConfig(llm={"provider": "deepseek", "base_url": "https://api.deepseek.com", "model": "deepseek-chat"})
    router = ModelRouter(config)

    assert router.choose_provider() == "deepseek"
    assert router.choose_model() == "deepseek-chat"
    assert router.choose_base_url() == "https://api.deepseek.com"


def test_llm_client_records_summary_in_normal_mode() -> None:
    """普通模式只记录摘要。"""
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="hello"))], usage=None)
    config = AppConfig(llm={"api_key": "test", "base_url": "https://api.openai.com/v1"}, agent={"debug": False})
    client = OpenAICompatibleClient(config=config, client=FakeClient(response))

    result = client.chat([{"role": "user", "content": "hi"}])

    assert result.success is True
    assert result.data["content"] == "hello"
    assert client.client.chat.completions.last_kwargs["timeout"] == config.llm.timeout_seconds
    assert client.records[-1].summary.startswith("model=")
    assert client.records[-1].input_text == ""


def test_llm_client_returns_failure_on_request_error() -> None:
    """Request failures should become ToolResult failures instead of hanging the agent."""
    config = AppConfig(llm={"api_key": "test", "base_url": "https://api.openai.com/v1", "timeout_seconds": 2})
    client = OpenAICompatibleClient(config=config, client=FakeClient(TimeoutError("request timed out")))

    result = client.chat([{"role": "user", "content": "hi"}])

    assert result.success is False
    assert result.exit_code == 1
    assert "request timed out" in result.stderr_summary


def test_llm_client_records_debug_details_with_sanitization() -> None:
    """debug 模式应记录脱敏后的输入输出与耗时。"""
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="token=abc123"))], usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2, total_tokens=3, model_dump=lambda: {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}))
    config = AppConfig(llm={"api_key": "test", "base_url": "https://api.openai.com/v1"}, agent={"debug": True})
    client = OpenAICompatibleClient(config=config, client=FakeClient(response))

    client.chat([{"role": "user", "content": "Authorization: Bearer secret-token"}])

    record = client.records[-1]
    assert "secret-token" not in record.input_text
    assert "abc123" not in record.output_text
    assert record.latency_ms is not None
