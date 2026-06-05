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
    test_command: str = 'mvn "-DargLine=-XX:+EnableDynamicAgentLoading -Xshare:off" test'
    lint_command: str = ""
    allowed_commands: list[str] = Field(default_factory=lambda: ["mvn", "git", "python", "pytest", "ruff", "java", "javac"])


class LLMConfig(BaseModel):
    """定义大模型服务接入配置。"""

    provider: str = "doubao"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-5.2-codex-compatible"
    timeout_seconds: int = 60
    fallback_provider: str = ""
    fallback_base_url: str = ""
    fallback_api_key: str = ""
    fallback_model: str = ""


class CompactConfig(BaseModel):
    """定义 Legacy Full Compact 的触发阈值和上下文恢复预算。"""

    enabled: bool = True
    # 不同 OpenAI-compatible 供应商的模型命名不统一，因此窗口大小必须显式配置。
    context_window_tokens: int = 1_000_000
    # 摘要请求需要预留输出空间，否则 compact 请求自身也可能超过模型窗口。
    summary_max_output_tokens: int = 20_000
    # 主修复请求仍要为模型的下一步工具调用和解释预留输出空间。
    normal_output_reserve_tokens: int = 40_000
    # 在真正达到模型极限前提前 compact，为估算误差和工具 schema 留出余量。
    buffer_tokens: int = 100_000
    max_ptl_retries: int = 3
    max_consecutive_failures: int = 3
    # 最近轮次按完整 assistant(tool_calls) -> tool(result) 组合保留。
    keep_recent_rounds: int = 8
    restore_max_files: int = 8
    restore_max_chars_per_file: int = 16_000
    restore_total_chars: int = 80_000


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
    review_callback_mode: str = "local"
    review_callback_host: str = "127.0.0.1"
    review_callback_port: int = 8765
    review_callback_base_url: str = "http://127.0.0.1:8765"


class FeishuKnowledgeConfig(BaseModel):
    """Configure Feishu OpenAPI as a knowledge source for local RAG sync."""

    enabled: bool = False
    app_id: str = ""
    app_secret: str = ""
    wiki_space_ids: list[str] = Field(default_factory=list)
    sync_interval_minutes: int = 60
    base_url: str = "https://open.feishu.cn/open-apis"


class SessionConfig(BaseModel):
    """定义会话、状态与产物存储配置。"""

    backend: str = "sqlite"
    root_dir: str = "./data/sessions"
    db_path: str = "./data/sessions/agent.db"
    retention_days: int = 7


class AgentConfig(BaseModel):
    """定义代理运行策略配置。"""

    name: str = "auto-fix-agent"
    max_retry: int = 3
    reflection_enabled: bool = True
    review_required: bool = True
    watch_paths: list[str] = Field(default_factory=lambda: ["./logs"])
    debug: bool = False


class RagRetrievalConfig(BaseModel):
    """Configure hybrid retrieval and parent-result aggregation."""

    bm25_weight: float = 1.2
    vector_weight: float = 1.0
    rrf_k: int = 60
    vector_min_score: float = 0.25
    skill_child_top_k: int = 20
    parent_skill_top_k: int = 3
    project_doc_recall_top_k: int = 20
    project_doc_final_top_k: int = 5
    candidate_top_n: int = 15
    per_doc_chunk_cap: int = 2


class RagContextSynthesizerConfig(BaseModel):
    """Configure the optional LLM-backed RAG context synthesizer."""

    enabled: bool = True
    inherit_main_llm: bool = True
    provider: str = ""
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    timeout_seconds: int = 60
    max_output_tokens: int = 4000


class RagRerankConfig(BaseModel):
    """Configure optional Qwen rerank after hybrid RRF recall."""

    enabled: bool = True
    provider: str = "qwen_openai_compatible"
    base_url: str = "https://dashscope.aliyuncs.com/compatible-api/v1"
    api_key: str = ""
    model: str = "qwen3-rerank"
    timeout_seconds: int = 30
    top_n: int = 30
    skill_min_score: float = 0.20
    project_doc_min_score: float = 0.25
    skill_min_keep: int = 1
    project_doc_min_keep: int = 1
    passed_skill_quota: int = 2
    failed_skill_quota: int = 2
    validation_skill_quota: int = 1
    project_doc_quota: int = 5
    document_max_chars: int = 2000
    skill_instruct: str = (
        "Given a Java repair bug query, rank historical repair skills by usefulness "
        "for diagnosing and fixing the bug."
    )
    project_doc_instruct: str = (
        "Given a Java repair bug query, rank project documents by whether they contain "
        "current business constraints, API rules, database rules, or validation requirements "
        "relevant to the bug."
    )


class RagConfig(BaseModel):
    """Configure local RAG indexing and retrieval."""

    enabled: bool = True
    backend: str = "sqlite"
    db_path: str = "./data/sessions/agent.db"
    auto_index_on_startup: bool = False
    dynamic_tools_enabled: bool = False
    debug_candidate_logging: bool = False

    top_k_skills: int = 3
    min_score: float = 0.25
    embedding_provider: str = "fallback"
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = ""
    embedding_dimensions: int = 1024

    module_aliases: dict[str, str] = Field(default_factory=dict)
    retrieval: RagRetrievalConfig = Field(default_factory=RagRetrievalConfig)
    rerank: RagRerankConfig = Field(default_factory=RagRerankConfig)
    context_synthesizer: RagContextSynthesizerConfig = Field(default_factory=RagContextSynthesizerConfig)


class AppConfig(BaseModel):
    """聚合系统运行所需的全部配置分组。"""

    env: str = "dev"
    workspace: str = "."
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    compact: CompactConfig = Field(default_factory=CompactConfig)
    gitee: GiteeConfig = Field(default_factory=GiteeConfig)
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)
    feishu_knowledge: FeishuKnowledgeConfig = Field(default_factory=FeishuKnowledgeConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    rag: RagConfig = Field(default_factory=RagConfig)


def load_config(path: str) -> AppConfig:
    """从指定 YAML 文件加载配置并返回应用配置对象。"""
    config_path = Path(path)
    if not config_path.exists():
        return AppConfig()

    raw: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(raw)
