from __future__ import annotations

"""定义日志目录监听的最小实现。"""

from pathlib import Path


class LogWatcher:
    """负责保存待监听路径并返回监听状态。"""

    def __init__(self, paths: list[str]) -> None:
        """将字符串路径列表转换为 Path 对象列表。"""
        self.paths = [Path(path) for path in paths]

    def watch(self) -> str:
        """返回当前监听路径数量的摘要信息。"""
        return f"watching {len(self.paths)} path(s)"
