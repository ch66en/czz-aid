from __future__ import annotations

"""提供根据 Java traceback 读取上下文代码片段的工具。"""

from pathlib import Path
from typing import Any

from agent.code_nav.ast_symbols import JavaAstSymbolExtractor
from agent.config import AppConfig
from agent.ingestion.traceback_parser import TracebackParser
from agent.models import StackFrame, ToolResult, ToolSpec
from agent.tools.base import BaseTool, PermissionType


class ReadStackContextTool(BaseTool):
	"""把异常栈帧映射到源码符号，并返回代码上下文。"""

	def __init__(
		self,
		config: AppConfig,
		parser: TracebackParser | None = None,
		extractor: JavaAstSymbolExtractor | None = None,
	) -> None:
		self.config = config
		self.parser = parser or TracebackParser()
		self.extractor = extractor or JavaAstSymbolExtractor()

	@property
	def spec(self) -> ToolSpec:
		return ToolSpec(
			name="read_stack_context",
			description="Resolve stack frames to source symbol context",
			input_schema={
				"type": "object",
				"properties": {
					"traceback": {"type": "string"},
					"frames": {"type": "array"},
					"package_prefix": {"type": "string"},
				},
			},
			permission=PermissionType.READ_ONLY.value,
			executor="local",
		)

	@property
	def permission(self) -> PermissionType:
		return PermissionType.READ_ONLY

	def run(self, payload: dict[str, Any] | None = None) -> ToolResult:
		data = payload or {}
		package_prefix = str(data.get("package_prefix") or "").strip() or None
		frames = self._load_frames(data, package_prefix)

		contexts: list[dict[str, Any]] = []
		artifacts: list[str] = []
		for frame in frames:
			if package_prefix and not frame.module_name.startswith(package_prefix):
				continue

			source_path = self._resolve_source_path(frame)
			if source_path is None:
				continue

			resolved = self._resolve_symbol(source_path, frame)
			if resolved is None:
				continue

			contexts.append(
				{
					"filePath": str(source_path),
					"moduleName": frame.module_name,
					"functionName": frame.function_name,
					"lineNumber": frame.line_number,
					"symbol": resolved["symbol"],
					"code": resolved["code"],
					"contentHash": resolved["contentHash"],
				}
			)
			artifacts.append(str(source_path))

		return ToolResult(
			tool="read_stack_context",
			success=True,
			exit_code=0,
			stdout_summary=f"resolved {len(contexts)} context(s)",
			data={"contexts": contexts},
			artifacts=sorted(set(artifacts)),
		)

	def _load_frames(self, payload: dict[str, Any], package_prefix: str | None) -> list[StackFrame]:
		if isinstance(payload.get("frames"), list):
			result: list[StackFrame] = []
			for raw in payload["frames"]:
				if not isinstance(raw, dict):
					continue
				result.append(
					StackFrame(
						file_path=str(raw.get("filePath") or raw.get("file_path") or ""),
						function_name=str(raw.get("functionName") or raw.get("function_name") or ""),
						line_number=int(raw.get("lineNumber") or raw.get("line_number") or 0),
						module_name=str(raw.get("moduleName") or raw.get("module_name") or ""),
						code_line=str(raw.get("codeLine") or raw.get("code_line") or ""),
						is_business_code=bool(raw.get("isBusinessCode") or raw.get("is_business_code") or False),
					)
				)
			return result

		traceback_text = str(payload.get("traceback") or "")
		return self.parser.parse(traceback_text, package_prefix=package_prefix).frames

	def _resolve_source_path(self, frame: StackFrame) -> Path | None:
		project_root = Path(self.config.project.root)
		candidate_from_module: Path | None = None
		if frame.module_name:
			module_rel = Path(*frame.module_name.split(".")).with_suffix(".java")
			candidate_from_module = project_root / "src" / "main" / "java" / module_rel
			if candidate_from_module.exists():
				return candidate_from_module

		if frame.file_path:
			file_name = Path(frame.file_path).name
			for path in project_root.rglob(file_name):
				if path.suffix.lower() == ".java":
					return path

		if candidate_from_module and candidate_from_module.suffix.lower() == ".java":
			return candidate_from_module if candidate_from_module.exists() else None
		return None

	def _resolve_symbol(self, source_path: Path, frame: StackFrame) -> dict[str, Any] | None:
		try:
			return self.extractor.find_symbol_at(str(source_path), frame.line_number)
		except Exception:
			pass

		if not frame.function_name:
			return None

		try:
			extracted = self.extractor.extract(str(source_path))
		except Exception:
			return None

		symbols = extracted.get("symbols", [])
		target = None
		for symbol in symbols:
			if symbol.get("name") == frame.function_name and symbol.get("kind") in {"method", "constructor"}:
				target = symbol
				break
		if target is None:
			return None

		lines = source_path.read_text(encoding="utf-8").splitlines()
		start = int(target.get("startLine", 1))
		end = int(target.get("endLine", start))
		code = [{"line": idx + 1, "text": lines[idx]} for idx in range(start - 1, min(end, len(lines)))]
		raw_code = "\n".join(item["text"] for item in code)
		return {
			"filePath": str(source_path),
			"symbol": target,
			"code": code,
			"contentHash": self.extractor._content_hash(raw_code),
		}
