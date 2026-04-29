from __future__ import annotations

from urllib.request import urlopen
from urllib.error import HTTPError

from agent.ingestion.review_callback_server import ReviewCallbackServer


class FakeReflection:
    def __init__(self) -> None:
        self.payloads = []

    def handle_review_event(self, payload):
        self.payloads.append(payload)
        return type("Result", (), {"success": True, "message": "ok"})()


class RaisingReflection:
    def handle_review_event(self, payload):
        raise RuntimeError("skill path failed")


def test_review_callback_server_handles_review_passed() -> None:
    reflection = FakeReflection()
    server = ReviewCallbackServer("127.0.0.1", 0, reflection)  # type: ignore[arg-type]
    assert server.start() is True
    try:
        with urlopen(f"{server.base_url}/review?event_type=review_passed&bug_id=BUG-1", timeout=5) as response:
            body = response.read().decode("utf-8")
    finally:
        server.stop()

    assert "BUG-1" in body
    assert reflection.payloads == [{"event_type": "review_passed", "bug_id": "BUG-1", "reviewer": "local-review", "comment": ""}]


def test_review_callback_server_review_failed_requires_branch_form() -> None:
    reflection = FakeReflection()
    server = ReviewCallbackServer("127.0.0.1", 0, reflection)  # type: ignore[arg-type]
    assert server.start() is True
    try:
        with urlopen(f"{server.base_url}/review?event_type=review_failed&bug_id=BUG-2", timeout=5) as response:
            body = response.read().decode("utf-8")
    finally:
        server.stop()

    assert "human_fix_branch" in body
    assert reflection.payloads == []


def test_review_callback_server_returns_error_page_on_reflection_exception() -> None:
    server = ReviewCallbackServer("127.0.0.1", 0, RaisingReflection())  # type: ignore[arg-type]
    assert server.start() is True
    try:
        try:
            urlopen(f"{server.base_url}/review?event_type=review_passed&bug_id=BUG-3", timeout=5)
        except HTTPError as exc:
            body = exc.read().decode("utf-8")
            status = exc.code
    finally:
        server.stop()

    assert status == 500
    assert "skill path failed" in body
