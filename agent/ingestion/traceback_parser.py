from __future__ import annotations

"""解析 Java 异常栈中的关键信息。"""

from dataclasses import dataclass
import re
from typing import Iterable

from agent.models import StackFrame


_FRAME_PATTERN = re.compile(r"^at\s+(?P<class_method>[\w.$<>]+)\((?P<source>[^)]*)\)$")
_EXCEPTION_PATTERN = re.compile(r"^(?P<type>[\w.$]+)(?::\s*(?P<message>.*))?$")
_EXCEPTION_START_SEARCH = re.compile(
    r'(?:Exception in thread "[^"]+"\s+)?'
    r"(?P<type>(?:[A-Za-z_$][\w$]*\.)*[A-Za-z_$][\w$]*(?:Exception|Error|Throwable))"
    r"(?:[:\s].*)?"
)

_FRAMEWORK_PREFIXES = (
    "java.",
    "javax.",
    "jakarta.",
    "org.springframework.",
    "sun.",
    "com.sun.",
)


@dataclass(slots=True)
class ParsedTraceback:
    """表示从异常栈中提取出的结构化结果。"""

    exception_type: str
    message: str
    frames: list[StackFrame]
    top_business_frame: str
    normalized_trace: str


class TracebackParser:
    """负责将 Java 异常栈解析为结构化数据。"""

    def parse(self, text: str, package_prefix: str | None = None) -> ParsedTraceback:
        """解析输入异常栈并返回结构化结果。"""
        lines = [line.rstrip() for line in text.splitlines() if line.strip()]
        if not lines:
            return ParsedTraceback(
                exception_type="UnknownError",
                message="",
                frames=[],
                top_business_frame="",
                normalized_trace="",
            )

        lines = self._trim_to_exception(lines)
        exception_type, message = self._parse_exception_header(lines[0])
        frames = self._parse_frames(lines[1:])
        top_business = self._find_top_business_frame(frames, package_prefix)
        normalized_trace = "\n".join([lines[0], *[self._format_frame(frame) for frame in frames]])
        return ParsedTraceback(
            exception_type=exception_type,
            message=message,
            frames=frames,
            top_business_frame=top_business,
            normalized_trace=normalized_trace,
        )

    def _parse_exception_header(self, line: str) -> tuple[str, str]:
        """解析异常首行中的类型与消息。"""
        match = _EXCEPTION_PATTERN.match(line.strip())
        if not match:
            return "UnknownError", line.strip()
        return match.group("type"), match.group("message") or ""

    def _trim_to_exception(self, lines: list[str]) -> list[str]:
        for index, line in enumerate(lines):
            stripped = line.strip()
            match = _EXCEPTION_START_SEARCH.search(stripped)
            if match:
                return [stripped[match.start("type") :], *lines[index + 1 :]]
        return lines

    def _parse_frames(self, lines: Iterable[str]) -> list[StackFrame]:
        """解析异常栈中的每个堆栈帧。"""
        frames: list[StackFrame] = []
        for line in lines:
            match = _FRAME_PATTERN.match(line.strip())
            if not match:
                continue
            class_method = match.group("class_method")
            source = match.group("source")
            file_path, line_number = self._parse_source(source)
            function_name = class_method.split(".")[-1]
            module_name = ".".join(class_method.split(".")[:-1])
            frames.append(
                StackFrame(
                    file_path=file_path,
                    function_name=function_name,
                    line_number=line_number,
                    code_line="",
                    module_name=module_name,
                    is_business_code=self._is_business_frame(module_name, None),
                )
            )
        return frames

    def _parse_source(self, source: str) -> tuple[str, int]:
        """解析栈帧来源文本中的文件路径与行号。"""
        if ":" not in source:
            return source, 0
        file_path, raw_line = source.rsplit(":", 1)
        try:
            return file_path, int(raw_line)
        except ValueError:
            return source, 0

    def _find_top_business_frame(self, frames: list[StackFrame], package_prefix: str | None) -> str:
        """根据包名前缀或框架过滤规则选择首个业务栈帧。"""
        for frame in frames:
            if self._is_business_frame(frame.module_name, package_prefix):
                return self._format_business_frame(frame)
        return ""

    def _is_business_frame(self, module_name: str, package_prefix: str | None) -> bool:
        """判断某一栈帧是否属于业务代码。"""
        if package_prefix:
            return module_name.startswith(package_prefix)
        return not module_name.startswith(_FRAMEWORK_PREFIXES)

    def _format_business_frame(self, frame: StackFrame) -> str:
        """格式化业务栈帧为便于展示的字符串。"""
        return f"{frame.module_name}({frame.file_path}:{frame.line_number})"

    def _format_frame(self, frame: StackFrame) -> str:
        """把栈帧重新格式化为标准化文本。"""
        return f"at {frame.module_name}.{frame.function_name}({frame.file_path}:{frame.line_number})"
