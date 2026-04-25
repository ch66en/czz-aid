from __future__ import annotations

"""定义应用配置模型与 YAML 配置加载逻辑。"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ProjectConfig(BaseModel):
    """定义项目仓库与工作目录相关配置。"""

    name: str = "default-project"
    root: str = "."
    language: str = "java"
    default_branch: str = "main"
    compile_command: str = "mvn compile"
    test_command: str = "mvn test"


class LLMConfig(BaseModel):
    """定义大模型服务接入配置。"""

    provider: str = "doubao"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-5.2-codex-compatible"
    timeout_seconds: int = 60


class GiteeConfig(BaseModel):
    """定义 Gitee 集成所需配置。"""

    base_url: str = "https://gitee.com/api/v5"
    token: str = ""
    owner: str = ""
    repo: str = ""


class FeishuConfig(BaseModel):
    """定义飞书通知与审核相关配置。"""

    webhook: str = ""
    app_id: str = ""
    app_secret: str = ""
    approval_chat_id: str = ""


class SessionConfig(BaseModel):
    """定义会话、状态与产物存储配置。"""

    root_dir: str = "./data/sessions"
    retention_days: int = 7


class AgentConfig(BaseModel):
    """定义代理运行策略配置。"""

    name: str = "auto-fix-agent"
    max_retry: int = 3
    reflection_enabled: bool = True
    review_required: bool = True
    watch_paths: list[str] = Field(default_factory=lambda: ["./logs"])
    debug: bool = False


class AppConfig(BaseModel):
    """聚合系统运行所需的全部配置分组。"""

    env: str = "dev"
    workspace: str = "."
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    gitee: GiteeConfig = Field(default_factory=GiteeConfig)
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)


def load_config(path: str) -> AppConfig:
    """从指定 YAML 文件加载配置并返回应用配置对象。"""
    config_path = Path(path)
    if not config_path.exists():
        return AppConfig()

    raw: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(raw)
