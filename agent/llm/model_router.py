from __future__ import annotations

"""定义模型选择逻辑。"""

from agent.config import AppConfig


class ModelRouter:
    """根据配置选择模型与提供方。"""

    def __init__(self, config: AppConfig) -> None:
        """初始化模型路由器。"""
        self.config = config

    def choose_provider(self) -> str:
        """返回当前配置的提供方。"""
        return self.config.llm.provider or "doubao"

    def choose_model(self) -> str:
        """返回当前配置中指定的模型名称。"""
        return self.config.llm.model

    def choose_base_url(self) -> str:
        """返回当前配置中指定的 API 基础地址。"""
        provider = self.choose_provider()
        return self._resolve_base_url(provider, self.config.llm.base_url)

    def has_fallback(self) -> bool:
        """判断是否配置了备用 LLM。"""
        return bool(self.config.llm.fallback_api_key.strip())

    def choose_fallback_provider(self) -> str:
        """返回备用 LLM 的提供方。"""
        return self.config.llm.fallback_provider or self.choose_provider()

    def choose_fallback_model(self) -> str:
        """返回备用 LLM 的模型名称。"""
        return self.config.llm.fallback_model or self.choose_model()

    def choose_fallback_base_url(self) -> str:
        """返回备用 LLM 的 API 基础地址。"""
        provider = self.choose_fallback_provider()
        return self._resolve_base_url(provider, self.config.llm.fallback_base_url)

    def _resolve_base_url(self, provider: str, configured_url: str) -> str:
        """根据提供方和配置地址解析最终的 base URL。"""
        if configured_url:
            return configured_url
        if provider == "openai":
            return "https://api.openai.com/v1"
        if provider == "deepseek":
            return "https://api.deepseek.com"
        if provider == "qwen":
            return "https://dashscope.aliyuncs.com/compatible-mode/v1"
        return "https://ark.cn-beijing.volces.com/api/v3"
