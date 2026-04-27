from __future__ import annotations

"""提供命令行入口并装配系统组件。"""

import argparse
import os
from pathlib import Path
from typing import Any, Sequence

import agent.config as config_module
from agent.core.dedup_engine import DedupEngine
from agent.core.permission_guard import PermissionGuard
from agent.core.repair_agent import RepairAgent
from agent.core.task_manager import TaskManager
from agent.core.tool_registry import ToolRegistry
from agent.doctor.doctor import Doctor
from agent.ingestion.pipeline import IngestionPipeline
from agent.ingestion.sanitizer import Sanitizer
from agent.ingestion.traceback_parser import TracebackParser
from agent.llm.openai_compatible_client import OpenAICompatibleClient
from agent.reflection.reflection_subagent import ReflectionSubAgent
from agent.storage.session_store import SessionStore
from agent.storage.skill_store import SkillStore
from agent.storage.task_store import TaskStore

# 保留历史导出，兼容测试或外部 monkeypatch。
load_config = config_module.load_config


def _short_text(value: str, limit: int = 240) -> str:
    """Keep CLI summaries readable."""
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _format_repair_output(result: Any) -> str:
    """Return a compact repair summary for console output."""
    repair_result = getattr(result, "repair_result", result)
    bug_event = getattr(result, "bug_event", None)
    task = getattr(repair_result, "task", None)
    last_result = getattr(repair_result, "last_result", None)

    bug_id = getattr(task, "bug_id", "") or getattr(bug_event, "bug_id", "")
    lines = [
        f"repair {getattr(repair_result, 'status', 'unknown')}: {bug_id}".rstrip(),
        _short_text(getattr(repair_result, "message", "")),
    ]
    if last_result is not None:
        lines.append(f"last_tool: {getattr(last_result, 'tool', 'unknown')} success={getattr(last_result, 'success', False)}")
        summary = getattr(last_result, "stderr_summary", "") or getattr(last_result, "stdout_summary", "")
        if summary:
            lines.append(f"summary: {_short_text(summary)}")
    pr_url = getattr(task, "pr_url", "")
    if pr_url:
        lines.append(f"pr: {pr_url}")
    return "\n".join(line for line in lines if line)


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(prog="auto-fix-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor")
    subparsers.add_parser("watch")

    repair_parser = subparsers.add_parser("repair")
    repair_parser.add_argument("--bug-id", required=True)
    repair_parser.add_argument("--raw-log", default="")
    repair_parser.add_argument("--raw-log-path", default="")
    repair_parser.add_argument("--source", default="unknown")
    repair_parser.add_argument("--project", default="default-project")
    repair_parser.add_argument("--title", default="")
    repair_parser.add_argument("--request-path", default="")
    repair_parser.add_argument("--request-method", default="")
    repair_parser.add_argument("--package-prefix", default="")

    reflect_parser = subparsers.add_parser("reflect")
    reflect_parser.add_argument("--bug-id", required=True)
    reflect_parser.add_argument("--result", required=True, choices=["pass", "fail"])

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """根据命令行参数执行对应的代理流程。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    config = config_module.load_config("config.example.yaml")

    registry = ToolRegistry()
    permission_guard = PermissionGuard()
    task_store = TaskStore()
    session_store = SessionStore()
    skill_store = SkillStore()
    task_manager = TaskManager(task_store=task_store)
    llm_client = OpenAICompatibleClient(config=config) if config.llm.api_key or os.getenv("OPENAI_API_KEY") else None
    repair_agent = RepairAgent(
        config=config,
        registry=registry,
        permission_guard=permission_guard,
        task_manager=task_manager,
        session_store=session_store,
        skill_store=skill_store,
        llm_client=llm_client,
    )
    pipeline = IngestionPipeline(session_store=session_store, dedup_engine=DedupEngine(), sanitizer=Sanitizer(), traceback_parser=TracebackParser(), repair_agent=repair_agent)
    doctor = Doctor(config=config)
    reflection = ReflectionSubAgent(config=config, session_store=session_store, skill_store=skill_store, llm_client=llm_client)

    if args.command == "doctor":
        print(doctor.run())
        return 0
    if args.command == "watch":
        print("watch mode started")
        return 0
    if args.command == "repair":
        raw_log = args.raw_log
        if args.raw_log_path:
            raw_log = Path(args.raw_log_path).read_text(encoding="utf-8")
        result = pipeline.process(
            raw_text=raw_log,
            bug_id=args.bug_id,
            source=args.source,
            project=args.project,
            title=args.title,
            request_path=args.request_path,
            request_method=args.request_method,
            package_prefix=args.package_prefix or None,
        )
        print(_format_repair_output(result))
        return 0
    if args.command == "reflect":
        print(reflection.reflect(args.bug_id, args.result))
        return 0

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    """以脚本方式运行时启动命令行入口。"""
    raise SystemExit(main())
