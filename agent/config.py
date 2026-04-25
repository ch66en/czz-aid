from __future__ import annotations

"""定义应用配置模型与配置加载逻辑。"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class AppConfig(BaseModel):
    """定义应用基础运行参数。"""

    name: str = "auto-fix-agent"
    env: str = "dev"
    workspace: str = "."


class LLMConfig(BaseModel):
    """定义大模型接入配置。"""

    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-5.2-codex-compatible"


class StorageConfig(BaseModel):
    """定义持久化存储配置。"""

    sqlite_path: str = "./data/agent.db"


class IntegrationsConfig(BaseModel):
    """定义外部集成服务配置。"""

    feishu_webhook: str = ""
    gitee_token: str = ""


class WatchConfig(BaseModel):
    """定义日志监听相关配置。"""

    paths: list[str] = Field(default_factory=lambda: ["./logs"])


class AgentConfig(BaseModel):
    """聚合应用运行所需的全部配置分组。"""

    app: AppConfig = Field(default_factory=AppConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    integrations: IntegrationsConfig = Field(default_factory=IntegrationsConfig)
    watch: WatchConfig = Field(default_factory=WatchConfig)


def load_config(path: str | Path | None = None) -> AgentConfig:
    """从指定 YAML 文件加载配置，不存在时返回默认配置。"""
    if path is None:
        return AgentConfig()

    config_path = Path(path)
    if not config_path.exists():
        return AgentConfig()

    raw: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return AgentConfig.model_validate(raw)
