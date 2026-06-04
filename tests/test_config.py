"""验证配置加载逻辑。"""

from pathlib import Path
from textwrap import dedent

from agent.config import AppConfig, load_config


def test_load_config_returns_defaults_when_file_missing(tmp_path: Path) -> None:
    """配置文件不存在时应返回默认配置。"""
    config = load_config(str(tmp_path / "missing.yaml"))

    assert isinstance(config, AppConfig)
    assert config.project.name == "default-project"
    assert config.project.test_command == 'mvn "-DargLine=-XX:+EnableDynamicAgentLoading -Xshare:off" test'
    assert config.session.backend == "sqlite"
    assert config.agent.max_retry == 3
    assert config.compact.enabled is True
    assert config.compact.context_window_tokens == 1_000_000
    assert config.compact.keep_recent_rounds == 8
    assert config.rag.auto_index_on_startup is False
    assert config.rag.dynamic_tools_enabled is False
    assert config.rag.retrieval.parent_skill_top_k == 3
    assert config.rag.context_synthesizer.enabled is True


def test_load_config_reads_yaml_values(tmp_path: Path) -> None:
    """配置加载应正确解析 YAML 中的嵌套字段。"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        dedent(
            """
            env: prod
            workspace: /workspace/app
            project:
              name: order-service
              root: /repo/order-service
              language: java
              default_branch: release
            llm:
              model: gpt-5.2-codex-compatible
              api_key: test-key
            compact:
              enabled: false
              context_window_tokens: 64000
              keep_recent_rounds: 2
            gitee:
              owner: demo
              repo: auto-fix-agent
            feishu:
              webhook: https://open.feishu.cn/webhook
              review_callback_mode: local
              review_callback_port: 8765
            session:
              backend: sqlite
              root_dir: /tmp/sessions
              db_path: /tmp/sessions/agent.db
            agent:
              max_retry: 5
              watch_paths:
                - ./runtime/logs
            rag:
              auto_index_on_startup: true
              dynamic_tools_enabled: true
              module_aliases:
                com.example.order: order
              retrieval:
                bm25_weight: 2.0
                parent_skill_top_k: 4
              context_synthesizer:
                enabled: false
            """
        ).strip(),
        encoding="utf-8",
    )

    config = load_config(str(config_path))

    assert config.env == "prod"
    assert config.workspace == "/workspace/app"
    assert config.project.name == "order-service"
    assert config.project.default_branch == "release"
    assert config.llm.api_key == "test-key"
    assert config.compact.enabled is False
    assert config.compact.context_window_tokens == 64000
    assert config.compact.keep_recent_rounds == 2
    assert config.gitee.repo == "auto-fix-agent"
    assert config.feishu.webhook == "https://open.feishu.cn/webhook"
    assert config.feishu.review_callback_mode == "local"
    assert config.feishu.review_callback_port == 8765
    assert config.session.backend == "sqlite"
    assert config.session.root_dir == "/tmp/sessions"
    assert config.session.db_path == "/tmp/sessions/agent.db"
    assert config.agent.max_retry == 5
    assert config.agent.watch_paths == ["./runtime/logs"]
    assert config.rag.auto_index_on_startup is True
    assert config.rag.dynamic_tools_enabled is True
    assert config.rag.module_aliases == {"com.example.order": "order"}
    assert config.rag.retrieval.bm25_weight == 2.0
    assert config.rag.retrieval.parent_skill_top_k == 4
    assert config.rag.context_synthesizer.enabled is False
