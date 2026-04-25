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
        if provider == "openai":
            return self.config.llm.base_url or "https://api.openai.com/v1"
        if provider == "deepseek":
            return self.config.llm.base_url or "https://api.deepseek.com"
        if provider == "qwen":
            return self.config.llm.base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        return self.config.llm.base_url or "https://ark.cn-beijing.volces.com/api/v3"
