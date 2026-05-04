from __future__ import annotations

"""提供源码文件读取工具。"""

from hashlib import sha256
from pathlib import Path
from typing import Any

from agent.config import AppConfig
from agent.models import ToolResult, ToolSpec
from agent.tools.base import BaseTool, PermissionType


class ReadCodeTool(BaseTool):
    """读取指定源码文件并返回文本内容。"""

    def __init__(self, config: AppConfig | None = None) -> None:
        self.config = config

    @property
    def spec(self) -> ToolSpec:
        """返回源码读取工具的规格说明。"""
        return ToolSpec(
            name="read_code",
            description="Read a code file or line range. Accepts absolute path, project-relative path, or bare filename (auto-resolved via project search).",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or project-relative path to a code file."},
                    "start_line": {"type": "integer", "description": "Optional 1-based first line to read."},
                    "end_line": {"type": "integer", "description": "Optional 1-based last line to read."},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            permission=PermissionType.READ_ONLY.value,
            executor="local",
        )

    @property
    def permission(self) -> PermissionType:
        return PermissionType.READ_ONLY

    def run(self, payload: dict[str, Any] | None = None) -> ToolResult:
        data = payload or {}
        path = self._resolve_path(str(data.get("path", "")))
        if not path.exists():
            return ToolResult(tool="read_code", success=False, exit_code=1, stderr_summary="code file not found", data={"path": str(path)}, artifacts=[])
        if path.is_dir():
            return ToolResult(tool="read_code", success=False, exit_code=1, stderr_summary="path is a directory, not a file", data={"path": str(path)}, artifacts=[])
        lines = path.read_text(encoding="utf-8").splitlines()
        start_line = data.get("start_line")
        end_line = data.get("end_line")
        if start_line is not None or end_line is not None:
            start = int(start_line or 1)
            end = int(end_line or len(lines))
            if start < 1 or end < start:
                return ToolResult(tool="read_code", success=False, exit_code=1, stderr_summary="invalid line range", data={"path": str(path), "start_line": start, "end_line": end}, artifacts=[])
            selected = lines[start - 1 : end]
            content = "\n".join(f"{idx} | {text}" for idx, text in enumerate(selected, start=start))
            raw = "\n".join(selected)
            return ToolResult(tool="read_code", success=True, exit_code=0, stdout_summary="code range loaded", data={"path": str(path), "startLine": start, "endLine": end, "totalLines": len(lines), "content": content, "contentHash": self._content_hash(raw)}, artifacts=[str(path)])
        if len(lines) > 300:
            return ToolResult(tool="read_code", success=False, exit_code=1, stderr_summary="file too large, please use ast_symbols or read_symbol_at to locate a smaller range", data={"path": str(path), "totalLines": len(lines)}, artifacts=[])
        content = "\n".join(f"{idx} | {text}" for idx, text in enumerate(lines, start=1))
        return ToolResult(tool="read_code", success=True, exit_code=0, stdout_summary="code file loaded", data={"path": str(path), "totalLines": len(lines), "content": content, "contentHash": self._content_hash("\n".join(lines))}, artifacts=[str(path)])

    def _content_hash(self, text: str) -> str:
        return f"sha256:{sha256(text.encode('utf-8')).hexdigest()}"

    def _resolve_path(self, raw_path: str) -> Path:
        path = Path(raw_path).expanduser()
        if path.is_absolute() or self.config is None:
            return path
        project_root = Path(self.config.project.root).expanduser()
        resolved = project_root / path
        # 短路径直接拼接存在时返回
        if resolved.exists():
            return resolved
        # 短文件名（无目录分隔符）：在项目内搜索匹配文件
        if len(path.parts) == 1:
            matches = sorted(project_root.rglob(path.name))
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                # 优先选 src/main/java 下的
                for m in matches:
                    if "src" in str(m) and "main" in str(m):
                        return m
                return matches[0]
        return resolved
