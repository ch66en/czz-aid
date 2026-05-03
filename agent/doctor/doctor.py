from __future__ import annotations

"""提供基础环境自检能力。"""

from agent.config import AppConfig


class Doctor:
    """执行系统最小健康检查并输出结果。"""

    def __init__(self, config: AppConfig) -> None:
        """初始化健康检查组件。"""
        self.config = config

    def run(self) -> str:
        """返回当前应用配置的健康检查摘要。"""
        return f"doctor ok: name={self.config.agent.name}, env={self.config.env}"
