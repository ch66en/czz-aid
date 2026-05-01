from __future__ import annotations

"""终端 UI 工具：提供启动 banner、旋转动画和状态指示器。"""

import sys
import threading
import time
from typing import TextIO


# ── ANSI 颜色 ────────────────────────────────────────────────

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"

_CYAN = "\033[36m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_MAGENTA = "\033[35m"
_BLUE = "\033[34m"
_WHITE = "\033[97m"
_GRAY = "\033[90m"


def _supports_color(stream: TextIO) -> bool:
    """判断终端是否支持 ANSI 颜色。"""
    if hasattr(stream, "isatty") and not stream.isatty():
        return False
    if sys.platform == "win32":
        try:
            import os
            os.system("")
        except Exception:
            return False
    return True


_USE_COLOR = _supports_color(sys.stdout)


def _c(code: str, text: str) -> str:
    """包裹颜色代码。"""
    if not _USE_COLOR:
        return text
    return f"{code}{text}{_RESET}"


# ── 启动 Banner ──────────────────────────────────────────────

_BANNER_LINES = [
    r"  $$$$$$\  $$$$$$$$\ $$$$$$$$\        $$$$$$\  $$$$$$\ $$$$$$$\  ",
    r" $$  __$$\ \____$$  |\____$$  |      $$  __$$\ \_$$  _|$$  __$$\ ",
    r" $$ /  \__|    $$  /     $$  /       $$ /  $$ |  $$ |  $$ |  $$ |",
    r" $$ |         $$  /     $$  /$$$$$$\ $$$$$$$$ |  $$ |  $$ |  $$ |",
    r" $$ |        $$  /     $$  / \______|$$  __$$ |  $$ |  $$ |  $$ |",
    r" $$ |  $$\  $$  /     $$  /          $$ |  $$ |  $$ |  $$ |  $$ |",
    r" \$$$$$$  |$$$$$$$$\ $$$$$$$$\       $$ |  $$ |$$$$$$\ $$$$$$$  |",
    r"  \______/ \________|\________|      \__|  \__|\______|\_______/ ",
]


def print_banner(version: str = "1.1.0") -> None:
    """显示启动 banner。"""
    for line in _BANNER_LINES:
        print(_c(_CYAN + _BOLD, line))
    sep = "=" * 72
    print(_c(_WHITE + _BOLD, f"       {sep}"))
    print(_c(_WHITE + _BOLD, f"        Auto-Fix Agent    v{version}"))
    print(_c(_WHITE + _BOLD, f"       {sep}"))
    print()


# ── 旋转动画 ─────────────────────────────────────────────────

_SPINNER_FRAMES = ["|", "/", "-", "\\"]


class Spinner:
    """后台旋转动画，在终端同一行显示 Thinking... 状态。"""

    def __init__(self, message: str = "Thinking") -> None:
        self._message = message
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._frame = 0

    def start(self) -> None:
        """启动旋转动画。"""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停止旋转动画并清除该行。"""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        # 清除旋转行
        sys.stdout.write("\r" + " " * 80 + "\r")
        sys.stdout.flush()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            frame = _SPINNER_FRAMES[self._frame % len(_SPINNER_FRAMES)]
            if _USE_COLOR:
                line = f"\r  {_MAGENTA}{frame}{_RESET} {_GRAY}{self._message}...{_RESET}"
            else:
                line = f"\r  {frame} {self._message}..."
            sys.stdout.write(line)
            sys.stdout.flush()
            self._frame += 1
            self._stop_event.wait(0.12)


# ── 状态指示器 ────────────────────────────────────────────────

def thinking(message: str = "Thinking") -> Spinner:
    """返回一个 Spinner 上下文管理器。用法:  with thinking(): ..."""
    return Spinner(message)


def tool_call(tool_name: str, detail: str = "") -> None:
    """工具调用。"""
    suffix = _c(_GRAY, f"  ({detail})") if detail else ""
    print(_c(_CYAN, f"  >> {tool_name}") + suffix, flush=True)


def step(message: str) -> None:
    """流程步骤。"""
    print(_c(_BLUE, f"  -- {message}"), flush=True)


def attempt(number: int, total: int, bug_id: str) -> None:
    """修复轮次。"""
    print()
    header = f"  === Attempt {number}/{total} ===  bug_id={bug_id}"
    print(_c(_YELLOW + _BOLD, header), flush=True)
    print()


def success(message: str) -> None:
    """成功。"""
    print(_c(_GREEN, f"  [OK] {message}"), flush=True)


def error(message: str) -> None:
    """失败。"""
    print(_c(_RED, f"  [FAIL] {message}"), flush=True)


def warning(message: str) -> None:
    """警告。"""
    print(_c(_YELLOW, f"  [WARN] {message}"), flush=True)


def info(message: str) -> None:
    """信息。"""
    print(_c(_GRAY, f"  [INFO] {message}"), flush=True)


def compile_result(success_: bool, exit_code: int) -> None:
    """编译结果。"""
    if success_:
        print(_c(_GREEN, "  [OK] Compile passed"), flush=True)
    else:
        print(_c(_RED, f"  [FAIL] Compile failed  ") + _c(_GRAY, f"exit_code={exit_code}"), flush=True)


def test_result(success_: bool, exit_code: int) -> None:
    """测试结果。"""
    if success_:
        print(_c(_GREEN, "  [OK] Test passed"), flush=True)
    else:
        print(_c(_RED, f"  [FAIL] Test failed  ") + _c(_GRAY, f"exit_code={exit_code}"), flush=True)


def rollback(restored: int, removed: int) -> None:
    """回滚结果。"""
    parts: list[str] = []
    if restored:
        parts.append(f"restored {restored} file(s)")
    if removed:
        parts.append(f"removed {removed} file(s)")
    summary = ", ".join(parts) or "nothing to rollback"
    print(_c(_YELLOW, f"  [ROLLBACK] {summary}"), flush=True)


def lint_check(passed: bool, output: str = "") -> None:
    """Lint 检查结果。"""
    if passed:
        print(_c(_GREEN, "  [OK] Lint passed"), flush=True)
    else:
        first_line = output.splitlines()[0][:120] if output else "unknown error"
        print(_c(_RED, f"  [FAIL] Lint failed  ") + _c(_GRAY, first_line), flush=True)


def denied(tool_name: str, reason: str) -> None:
    """权限拒绝。"""
    print(_c(_RED, f"  [DENIED] {tool_name}  ") + _c(_GRAY, reason), flush=True)


def pr_created(pr_url: str) -> None:
    """PR 创建成功。"""
    print(_c(_GREEN + _BOLD, f"  [PR CREATED] {pr_url}"), flush=True)


def review_requested(pr_url: str) -> None:
    """审核请求已发送。"""
    print(_c(_BLUE, f"  [REVIEW] {pr_url}"), flush=True)


def divider() -> None:
    """分隔线。"""
    print(_c(_DIM, "  " + "-" * 56), flush=True)


# ── Pipeline / Watch 专用 ────────────────────────────────────

def watch_detected(exception_type: str, bug_id: str, source: str) -> None:
    """日志监听检测到异常。"""
    print()
    divider()
    print(_c(_YELLOW + _BOLD, f"  [DETECTED] {exception_type}"), flush=True)
    print(_c(_GRAY, f"  bug_id : {bug_id}"), flush=True)
    print(_c(_GRAY, f"  source : {source}"), flush=True)
    divider()


def watch_result(status: str, success_: bool, message: str) -> None:
    """日志监听的修复结果。"""
    tag = _c(_GREEN, "[PASS]") if success_ else _c(_RED, "[FAIL]")
    print(_c(_WHITE + _BOLD, f"  {tag} status={status}  {message}"), flush=True)


def watch_last_tool(tool: str, success_: bool, exit_code: int) -> None:
    """日志监听的最后一个工具结果。"""
    tag = _c(_GREEN, "[OK]") if success_ else _c(_RED, "[FAIL]")
    print(f"  {tag} last tool={tool}  exit_code={exit_code}", flush=True)


def watch_last_error(stderr: str) -> None:
    """日志监听的最后一个错误。"""
    print(_c(_RED, f"  [ERR] {stderr}"), flush=True)


def watch_no_result() -> None:
    """日志监听无修复结果。"""
    print(_c(_GRAY, "  [SKIP] Pipeline finished without repair result"), flush=True)


def pipeline_bug(bug_id: str, exception: str, frames: int, duplicate: bool) -> None:
    """Pipeline 检测到 bug。"""
    dup_tag = _c(_YELLOW, " [DUPLICATE]") if duplicate else ""
    print(_c(_WHITE, f"  [PIPELINE] bug_id={bug_id}  exception={exception}  frames={frames}") + dup_tag, flush=True)


def pipeline_repair_start(bug_id: str) -> None:
    """Pipeline 开始修复。"""
    print(_c(_CYAN + _BOLD, f"  [REPAIR START] bug_id={bug_id}"), flush=True)


def pipeline_repair_finished(bug_id: str, status: str, success_: bool) -> None:
    """Pipeline 修复完成。"""
    tag = _c(_GREEN, "[DONE]") if success_ else _c(_RED, "[DONE]")
    print(_c(_WHITE + _BOLD, f"  {tag} bug_id={bug_id}  status={status}"), flush=True)


def pipeline_skip(bug_id: str, reason: str) -> None:
    """Pipeline 跳过修复。"""
    print(_c(_GRAY, f"  [SKIP] bug_id={bug_id}  reason={reason}"), flush=True)
