from __future__ import annotations

"""定义模型选择逻辑。"""

from agent.config import AgentConfig


class ModelRouter:
    """根据配置为代理选择默认模型。"""

    def __init__(self, config: AgentConfig) -> None:
        """初始化模型路由器。"""
        self.config = config

    def choose_model(self) -> str:
        """返回当前配置中指定的模型名称。"""
        return self.config.llm.model
