"""验证配置加载逻辑。"""

from pathlib import Path
from textwrap import dedent

from agent.config import AppConfig, load_config


def test_load_config_returns_defaults_when_file_missing(tmp_path: Path) -> None:
    """配置文件不存在时应返回默认配置。"""
    config = load_config(str(tmp_path / "missing.yaml"))

    assert isinstance(config, AppConfig)
    assert config.project.name == "default-project"
    assert config.agent.max_retry == 3


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
            gitee:
              owner: demo
              repo: auto-fix-agent
            feishu:
              webhook: https://open.feishu.cn/webhook
              review_callback_mode: local
              review_callback_port: 8765
            session:
              root_dir: /tmp/sessions
            agent:
              max_retry: 5
              watch_paths:
                - ./runtime/logs
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
    assert config.gitee.repo == "auto-fix-agent"
    assert config.feishu.webhook == "https://open.feishu.cn/webhook"
    assert config.feishu.review_callback_mode == "local"
    assert config.feishu.review_callback_port == 8765
    assert config.session.root_dir == "/tmp/sessions"
    assert config.agent.max_retry == 5
    assert config.agent.watch_paths == ["./runtime/logs"]
