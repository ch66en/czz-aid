"""验证备用 LLM 故障转移逻辑。"""

from types import SimpleNamespace

import pytest

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


def _ok_response(content: str = "ok") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))],
        usage=None,
    )


def test_primary_success_skips_fallback() -> None:
    """主 LLM 正常时不应触发备用 LLM。"""
    config = AppConfig(
        llm={
            "api_key": "primary-key",
            "fallback_api_key": "fallback-key",
            "fallback_model": "fallback-model",
        },
    )
    primary_client = FakeClient(_ok_response("primary"))
    client = OpenAICompatibleClient(config=config, client=primary_client)

    result = client.chat([{"role": "user", "content": "hi"}])

    assert result.success is True
    assert result.data["content"] == "primary"
    assert result.data.get("fallback_used") is None


def test_primary_failure_uses_fallback() -> None:
    """主 LLM 异常时应自动切换到备用 LLM。"""
    config = AppConfig(
        llm={
            "api_key": "primary-key",
            "fallback_api_key": "fallback-key",
            "fallback_model": "fallback-model",
        },
    )
    primary_client = FakeClient(ConnectionError("primary down"))
    client = OpenAICompatibleClient(config=config, client=primary_client)
    client.fallback_client = FakeClient(_ok_response("fallback ok"))

    result = client.chat([{"role": "user", "content": "hi"}])

    assert result.success is True
    assert result.data["content"] == "fallback ok"
    assert result.data["fallback_used"] is True
    assert result.data["model"] == "fallback-model"


def test_both_fail_returns_fallback_error() -> None:
    """主 LLM 和备用 LLM 都失败时，返回备用 LLM 的错误。"""
    config = AppConfig(
        llm={
            "api_key": "primary-key",
            "fallback_api_key": "fallback-key",
            "fallback_model": "fallback-model",
        },
    )
    primary_client = FakeClient(ConnectionError("primary down"))
    client = OpenAICompatibleClient(config=config, client=primary_client)
    client.fallback_client = FakeClient(TimeoutError("fallback timeout"))

    result = client.chat([{"role": "user", "content": "hi"}])

    assert result.success is False
    assert "fallback timeout" in result.stderr_summary


def test_no_fallback_configured_behaves_as_before() -> None:
    """未配置备用 LLM 时，行为应与原来一致。"""
    config = AppConfig(llm={"api_key": "primary-key"})
    primary_client = FakeClient(ConnectionError("connection failed"))
    client = OpenAICompatibleClient(config=config, client=primary_client)

    result = client.chat([{"role": "user", "content": "hi"}])

    assert result.success is False
    assert "connection failed" in result.stderr_summary
    assert client.fallback_client is None


def test_ping_primary_ok() -> None:
    """主 LLM ping 成功时不触发备用。"""
    config = AppConfig(
        llm={
            "api_key": "primary-key",
            "fallback_api_key": "fallback-key",
        },
    )
    client = OpenAICompatibleClient(config=config, client=FakeClient(_ok_response()))
    client.ping()


def test_ping_primary_fails_fallback_ok() -> None:
    """主 LLM ping 失败但备用成功时不应抛异常。"""
    config = AppConfig(
        llm={
            "api_key": "primary-key",
            "fallback_api_key": "fallback-key",
        },
    )
    client = OpenAICompatibleClient(config=config, client=FakeClient(ConnectionError("down")))
    client.fallback_client = FakeClient(_ok_response())

    client.ping()


def test_ping_both_fail_raises() -> None:
    """主 LLM 和备用 LLM ping 都失败时应抛异常。"""
    config = AppConfig(
        llm={
            "api_key": "primary-key",
            "fallback_api_key": "fallback-key",
        },
    )
    client = OpenAICompatibleClient(config=config, client=FakeClient(ConnectionError("primary down")))
    client.fallback_client = FakeClient(ConnectionError("fallback down"))

    with pytest.raises(RuntimeError, match="primary=.*fallback="):
        client.ping()


def test_model_router_has_fallback() -> None:
    """ModelRouter 应正确判断是否配置了备用 LLM。"""
    config_with = AppConfig(llm={"fallback_api_key": "key"})
    config_without = AppConfig(llm={})

    assert ModelRouter(config_with).has_fallback() is True
    assert ModelRouter(config_without).has_fallback() is False


def test_model_router_fallback_defaults() -> None:
    """未指定 fallback provider/model 时应沿用主配置。"""
    config = AppConfig(llm={
        "provider": "deepseek",
        "model": "deepseek-chat",
        "fallback_api_key": "key",
    })
    router = ModelRouter(config)

    assert router.choose_fallback_provider() == "deepseek"
    assert router.choose_fallback_model() == "deepseek-chat"


def test_model_router_fallback_explicit_values() -> None:
    """显式指定 fallback provider/model 时应使用指定值。"""
    config = AppConfig(llm={
        "provider": "doubao",
        "model": "doubao-pro",
        "fallback_provider": "openai",
        "fallback_model": "gpt-4o",
        "fallback_api_key": "key",
    })
    router = ModelRouter(config)

    assert router.choose_fallback_provider() == "openai"
    assert router.choose_fallback_model() == "gpt-4o"


def test_fallback_with_tool_calls() -> None:
    """备用 LLM 应支持 function calling。"""
    tool_call = SimpleNamespace(
        id="call-fb-1",
        type="function",
        function=SimpleNamespace(name="read_code", arguments='{"path":"A.java"}'),
    )
    fallback_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[tool_call]))],
        usage=None,
    )
    config = AppConfig(
        llm={
            "api_key": "primary-key",
            "fallback_api_key": "fallback-key",
            "fallback_model": "fallback-model",
        },
    )
    client = OpenAICompatibleClient(config=config, client=FakeClient(ConnectionError("down")))
    client.fallback_client = FakeClient(fallback_response)

    result = client.chat(
        [{"role": "user", "content": "read"}],
        tools=[{"type": "function", "function": {"name": "read_code", "parameters": {}}}],
    )

    assert result.success is True
    assert result.data["fallback_used"] is True
    assert result.data["tool_calls"][0]["function"]["name"] == "read_code"
