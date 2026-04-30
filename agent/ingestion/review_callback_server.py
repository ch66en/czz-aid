from __future__ import annotations

"""Local review callback server for Feishu URL buttons."""

from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from agent.models import ReviewDecision
from agent.reflection.reflection_subagent import ReflectionSubAgent


class ReviewCallbackServer:
    """Expose localhost review URLs that forward to ReflectionSubAgent."""

    def __init__(self, host: str, port: int, reflection: ReflectionSubAgent) -> None:
        self.host = host
        self.port = port
        self.reflection = reflection
        self._server: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> bool:
        if self._server is not None:
            return True

        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parent._handle_get(self)

            def log_message(self, format: str, *args: Any) -> None:
                return

        try:
            self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        except OSError as exc:
            print(f"[review-callback] disabled error={exc}", flush=True)
            return False
        self.port = int(self._server.server_address[1])
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        print(f"[review-callback] listening {self.base_url}", flush=True)
        return True

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None

    def _handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(handler.path)
        if parsed.path != "/review":
            self._send_html(handler, 404, "Not Found", "<p>unknown path</p>")
            return

        params = {key: values[-1] for key, values in parse_qs(parsed.query, keep_blank_values=True).items()}
        event_type = params.get("event_type", "")
        bug_id = params.get("bug_id", "")
        if not bug_id or event_type not in {ReviewDecision.REVIEW_PASSED.value, ReviewDecision.REVIEW_FAILED.value}:
            self._send_html(handler, 400, "Bad Request", "<p>missing or invalid review parameters</p>")
            return

        if event_type == ReviewDecision.REVIEW_FAILED.value and not params.get("human_fix_branch"):
            self._send_html(handler, 200, "Review Failed", self._failure_form(params))
            return

        payload = {
            "event_type": event_type,
            "bug_id": bug_id,
            "reviewer": params.get("reviewer", "local-review"),
            "comment": params.get("comment", ""),
        }
        if params.get("human_fix_branch"):
            payload["human_fix_branch"] = params["human_fix_branch"]

        try:
            result = self.reflection.handle_review_event(payload)
        except Exception as exc:
            print(f"[review-callback] review failed bug_id={bug_id} error={exc}", flush=True)
            body = (
                f"<p>审核事件：{escape(event_type)}</p>"
                f"<p>Bug ID：{escape(bug_id)}</p>"
                "<p>本地反思流程执行失败。</p>"
                f"<pre>{escape(str(exc))}</pre>"
            )
            self._send_html(handler, 500, "Review Failed", body)
            return
        title = "Review Accepted" if result.success else "Review Failed"
        status = "成功" if result.success else "失败"
        body = (
            f"<p>审核事件：{escape(event_type)}</p>"
            f"<p>Bug ID：{escape(bug_id)}</p>"
            f"<p>处理结果：{status}</p>"
            f"<pre>{escape(result.message)}</pre>"
        )
        self._send_html(handler, 200 if result.success else 500, title, body)

    def _failure_form(self, params: dict[str, str]) -> str:
        bug_id = params.get("bug_id", "")
        query = urlencode({"event_type": ReviewDecision.REVIEW_FAILED.value, "bug_id": bug_id, "reviewer": params.get("reviewer", "local-review")})
        return (
            f"<p>Bug ID：{escape(bug_id)}</p>"
            "<p>审核失败需要填写人工修复分支 human_fix_branch。</p>"
            "<form method='get' action='/review'>"
            f"<input type='hidden' name='event_type' value='{ReviewDecision.REVIEW_FAILED.value}'/>"
            f"<input type='hidden' name='bug_id' value='{escape(bug_id)}'/>"
            "<label>human_fix_branch: <input name='human_fix_branch' style='width: 360px'/></label>"
            "<button type='submit'>提交审核失败</button>"
            "</form>"
            f"<p><small>{escape(query)}</small></p>"
        )

    def _send_html(self, handler: BaseHTTPRequestHandler, status: int, title: str, body: str) -> None:
        html = (
            "<!doctype html><html><head><meta charset='utf-8'/>"
            f"<title>{escape(title)}</title>"
            "<style>body{font-family:Segoe UI,Arial,sans-serif;max-width:760px;margin:40px auto;line-height:1.6}"
            "pre{white-space:pre-wrap;background:#f6f8fa;padding:12px;border-radius:6px}</style>"
            "</head><body>"
            f"<h2>{escape(title)}</h2>{body}"
            "</body></html>"
        ).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(html)))
        handler.end_headers()
        try:
            handler.wfile.write(html)
        except OSError as exc:
            print(f"[review-callback] client disconnected while sending response error={exc}", flush=True)
