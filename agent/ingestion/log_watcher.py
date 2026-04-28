from __future__ import annotations

"""Tail log files and forward complete Java tracebacks to ingestion."""

from dataclasses import dataclass, field
import hashlib
import re
import time
from pathlib import Path
from typing import Any


_TRACE_START_PATTERN = re.compile(
    r'(?:Exception in thread "[^"]+"\s+)?'
    r"(?P<type>(?:[A-Za-z_$][\w$]*\.)*[A-Za-z_$][\w$]*(?:Exception|Error|Throwable))"
    r"(?:[:\s].*)?$"
)
_TRACE_START_SEARCH = re.compile(
    r'(?:Exception in thread "[^"]+"\s+)?'
    r"(?P<type>(?:[A-Za-z_$][\w$]*\.)*[A-Za-z_$][\w$]*(?:Exception|Error|Throwable))"
    r"(?:[:\s].*)?"
)
_FRAME_SEARCH = re.compile(r"(at\s+[\w.$<>/]+\([^)]*\))\s*$")
_CAUSE_SEARCH = re.compile(r"(Caused by:\s+.*)$")
_SUPPRESSED_SEARCH = re.compile(r"(Suppressed:\s+.*)$")
_OMITTED_SEARCH = re.compile(r"(\.\.\.\s+\d+\s+(?:common frames omitted|more))\s*$")


@dataclass
class _FileState:
    offset: int
    size: int = 0
    mtime_ns: int = 0
    signature: bytes = b""
    partial_line: str = ""
    pending_lines: list[str] = field(default_factory=list)
    last_trace_at: float = 0.0


_SIGNATURE_BYTES = 256


class LogWatcher:
    """Watch log files, extract Java stack traces, and call the pipeline."""

    def __init__(
        self,
        paths: list[str],
        pipeline: Any | None = None,
        project: str = "default-project",
        package_prefix: str | None = None,
        poll_interval: float = 1.0,
        idle_debounce: float = 1.5,
        seek_to_end: bool = True,
        encoding: str = "utf-8",
    ) -> None:
        self.paths = [Path(path) for path in paths]
        self.pipeline = pipeline
        self.project = project
        self.package_prefix = package_prefix
        self.poll_interval = poll_interval
        self.idle_debounce = idle_debounce
        self.seek_to_end = seek_to_end
        self.encoding = encoding
        self._states: dict[Path, _FileState] = {}
        self._initial_scan_complete = False

    def watch(self, max_iterations: int | None = None) -> str:
        """Continuously scan watched paths until interrupted."""
        message = f"watching {len(self.paths)} path(s)"
        print(message, flush=True)
        if self.pipeline is None and max_iterations is None:
            return message
        iterations = 0
        try:
            while max_iterations is None or iterations < max_iterations:
                self.scan_once()
                iterations += 1
                if max_iterations is None or iterations < max_iterations:
                    time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            self.flush_all()
        return message

    def scan_once(self) -> int:
        """Run one polling pass and return how many tracebacks were processed."""
        now = time.monotonic()
        processed = 0
        for path in self._discover_files():
            processed += self._read_new_content(path, now)
        self._initial_scan_complete = True
        processed += self._flush_ready(now)
        return processed

    def flush_all(self) -> int:
        """Flush every pending traceback that has at least one stack frame."""
        processed = 0
        for path in list(self._states):
            processed += self._flush_pending(path)
        return processed

    def _discover_files(self) -> list[Path]:
        files: list[Path] = []
        for path in self.paths:
            if self._has_glob(path):
                files.extend(candidate for candidate in path.parent.glob(path.name) if candidate.is_file())
                continue
            if path.is_dir():
                files.extend(candidate for candidate in path.iterdir() if candidate.is_file())
                continue
            if path.exists() and path.is_file():
                files.append(path)
        return sorted(set(files))

    def _has_glob(self, path: Path) -> bool:
        text = str(path)
        return any(char in text for char in "*?[")

    def _read_new_content(self, path: Path, now: float) -> int:
        try:
            stat = path.stat()
            size = stat.st_size
            mtime_ns = stat.st_mtime_ns
        except OSError:
            return 0

        state = self._state_for(path, size, mtime_ns)
        if self._was_rewritten(path, state, size, mtime_ns):
            print(f"[watch] detected rewritten log: {path}", flush=True)
            state.offset = 0
            state.partial_line = ""
            state.pending_lines.clear()
            state.signature = self._read_signature(path)

        if size == state.offset:
            state.size = size
            state.mtime_ns = mtime_ns
            return 0

        try:
            with path.open("rb") as handle:
                handle.seek(state.offset)
                data = handle.read()
                state.offset = handle.tell()
                state.size = size
                state.mtime_ns = mtime_ns
                state.signature = self._read_signature(path)
        except OSError:
            return 0

        if not data:
            return 0

        text = data.decode(self.encoding, errors="replace")
        lines = self._split_lines(state, text)
        processed = 0
        for line in lines:
            processed += self._consume_line(path, line, now)
        return processed

    def _state_for(self, path: Path, size: int, mtime_ns: int) -> _FileState:
        state = self._states.get(path)
        if state is not None:
            return state

        offset = size if self.seek_to_end and not self._initial_scan_complete else 0
        state = _FileState(offset=offset, size=size, mtime_ns=mtime_ns, signature=self._read_signature(path))
        self._states[path] = state
        return state

    def _was_rewritten(self, path: Path, state: _FileState, size: int, mtime_ns: int) -> bool:
        if size < state.offset:
            return True
        if state.offset == 0 or mtime_ns == state.mtime_ns:
            return False
        if size <= state.offset:
            return True
        if size > state.offset:
            return not self._read_signature(path).startswith(state.signature)
        return False

    def _read_signature(self, path: Path) -> bytes:
        try:
            with path.open("rb") as handle:
                return handle.read(_SIGNATURE_BYTES)
        except OSError:
            return b""

    def _split_lines(self, state: _FileState, text: str) -> list[str]:
        combined = f"{state.partial_line}{text}"
        if combined.endswith(("\n", "\r")):
            state.partial_line = ""
            return combined.splitlines()

        lines = combined.splitlines()
        if not lines:
            state.partial_line = combined
            return []
        state.partial_line = lines.pop()
        return lines

    def _consume_line(self, path: Path, line: str, now: float) -> int:
        state = self._states[path]
        body_line = self._clean_body_line(line)
        if body_line and state.pending_lines:
            state.pending_lines.append(body_line)
            state.last_trace_at = now
            return 0

        start_line = self._clean_start_line(line)
        if start_line:
            processed = 0
            if state.pending_lines:
                processed += self._flush_pending(path)
            state.pending_lines = [start_line]
            state.last_trace_at = now
            return processed
        return 0

    def _flush_ready(self, now: float) -> int:
        processed = 0
        for path, state in list(self._states.items()):
            if state.pending_lines and self._has_frame(state.pending_lines) and now - state.last_trace_at >= self.idle_debounce:
                processed += self._flush_pending(path)
        return processed

    def _flush_pending(self, path: Path) -> int:
        state = self._states.get(path)
        if state is None or not state.pending_lines:
            return 0
        if not self._has_frame(state.pending_lines):
            return 0

        clean_traceback = "\n".join(state.pending_lines).strip()
        state.pending_lines = []
        state.last_trace_at = 0.0
        if not clean_traceback or self.pipeline is None:
            return 0

        exception_type = self._exception_type(clean_traceback)
        bug_id = self._bug_id(path, clean_traceback)
        print(f"[watch] detected {exception_type} bug_id={bug_id} source={path}", flush=True)
        result = self.pipeline.process(
            raw_text=clean_traceback,
            bug_id=bug_id,
            source=f"log:{path}",
            project=self.project,
            title=f"Auto detected: {exception_type}",
            package_prefix=self.package_prefix,
        )
        self._print_pipeline_result(result)
        return 1

    def _print_pipeline_result(self, result: Any) -> None:
        repair_result = getattr(result, "repair_result", None)
        if repair_result is None:
            print("[watch] pipeline finished without repair result", flush=True)
            return
        print(
            f"[watch] repair status={repair_result.status} success={repair_result.success} "
            f"message={repair_result.message}",
            flush=True,
        )
        last_result = getattr(repair_result, "last_result", None)
        if last_result is not None:
            print(
                f"[watch] last tool={last_result.tool} success={last_result.success} "
                f"exit_code={last_result.exit_code}",
                flush=True,
            )
            if last_result.stderr_summary:
                print(f"[watch] last error={last_result.stderr_summary}", flush=True)

    def _clean_start_line(self, line: str) -> str:
        stripped = line.strip()
        if self._clean_body_line(stripped):
            return ""
        full_match = _TRACE_START_PATTERN.match(stripped)
        if full_match:
            return stripped[full_match.start("type") :]

        match = _TRACE_START_SEARCH.search(stripped)
        if not match:
            return ""
        return stripped[match.start("type") :]

    def _clean_body_line(self, line: str) -> str:
        stripped = line.strip()
        for pattern in (_FRAME_SEARCH, _CAUSE_SEARCH, _SUPPRESSED_SEARCH, _OMITTED_SEARCH):
            match = pattern.search(stripped)
            if match:
                return match.group(1)
        return ""

    def _has_frame(self, lines: list[str]) -> bool:
        return any(_FRAME_SEARCH.search(line.strip()) for line in lines)

    def _exception_type(self, clean_traceback: str) -> str:
        first_line = clean_traceback.splitlines()[0].strip()
        match = _TRACE_START_SEARCH.search(first_line)
        if match:
            return match.group("type")
        return "UnknownError"

    def _bug_id(self, path: Path, clean_traceback: str) -> str:
        digest = hashlib.sha1(f"{path}:{clean_traceback}".encode("utf-8")).hexdigest()[:12]
        return f"log-{digest}"
