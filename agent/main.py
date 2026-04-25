from __future__ import annotations

"""提供命令行入口并装配系统组件。"""

import argparse
from typing import Sequence

from agent.config import load_config
from agent.core.permission_guard import PermissionGuard
from agent.core.repair_agent import RepairAgent
from agent.core.task_manager import TaskManager
from agent.core.tool_registry import ToolRegistry
from agent.doctor.doctor import Doctor
from agent.reflection.reflection_subagent import ReflectionSubAgent
from agent.storage.session_store import SessionStore
from agent.storage.skill_store import SkillStore
from agent.storage.task_store import TaskStore


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(prog="auto-fix-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor")
    subparsers.add_parser("watch")

    repair_parser = subparsers.add_parser("repair")
    repair_parser.add_argument("--bug-id", required=True)

    reflect_parser = subparsers.add_parser("reflect")
    reflect_parser.add_argument("--bug-id", required=True)
    reflect_parser.add_argument("--result", required=True, choices=["pass", "fail"])

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """根据命令行参数执行对应的代理流程。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config()

    registry = ToolRegistry()
    permission_guard = PermissionGuard()
    task_store = TaskStore()
    session_store = SessionStore()
    skill_store = SkillStore()
    task_manager = TaskManager(task_store=task_store)
    repair_agent = RepairAgent(
        config=config,
        registry=registry,
        permission_guard=permission_guard,
        task_manager=task_manager,
        session_store=session_store,
        skill_store=skill_store,
    )
    doctor = Doctor(config=config)
    reflection = ReflectionSubAgent(task_store=task_store, skill_store=skill_store)

    if args.command == "doctor":
        print(doctor.run())
        return 0
    if args.command == "watch":
        print("watch mode started")
        return 0
    if args.command == "repair":
        print(repair_agent.repair(args.bug_id))
        return 0
    if args.command == "reflect":
        print(reflection.reflect(args.bug_id, args.result))
        return 0

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    """以脚本方式运行时启动命令行入口。"""
    raise SystemExit(main())
