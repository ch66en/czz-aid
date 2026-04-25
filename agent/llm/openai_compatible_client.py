from __future__ import annotations

"""提供兼容 OpenAI 接口风格的客户端封装。"""

from agent.config import LLMConfig


class OpenAICompatibleClient:
    """封装面向兼容接口模型的最小客户端行为。"""

    def __init__(self, config: LLMConfig) -> None:
        """初始化客户端配置。"""
        self.config = config

    def ping(self) -> str:
        """返回客户端当前绑定模型的可用性摘要。"""
        return f"client ready for {self.config.model}"
