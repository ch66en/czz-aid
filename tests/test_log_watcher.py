from __future__ import annotations

from pathlib import Path

from agent.ingestion.log_watcher import LogWatcher


class FakePipeline:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def process(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs


def test_log_watcher_without_pipeline_returns_status() -> None:
    watcher = LogWatcher(["./missing.log"])

    assert watcher.watch() == "watching 1 path(s)"


def test_log_watcher_extracts_traceback_from_mixed_log(tmp_path: Path) -> None:
    log_path = tmp_path / "app.log"
    log_path.write_text(
        "\n".join(
            [
                "2026-04-28 10:31:02 INFO request /api/user/1",
                "2026-04-28 10:31:02 ERROR request failed",
                '2026-04-28 10:31:02 ERROR java.lang.NullPointerException: Cannot invoke "User.getName()"',
                "2026-04-28 10:31:02 ERROR     at com.demo.service.UserService.getUserName(UserService.java:42)",
                "2026-04-28 10:31:02 ERROR     at com.demo.controller.UserController.getUser(UserController.java:28)",
                "2026-04-28 10:31:02 ERROR Caused by: java.lang.IllegalStateException: user not found",
                "2026-04-28 10:31:02 ERROR     at com.demo.repository.UserRepository.findById(UserRepository.java:19)",
                "2026-04-28 10:31:03 INFO response 500",
                "",
            ]
        ),
        encoding="utf-8",
    )
    pipeline = FakePipeline()
    watcher = LogWatcher(
        [str(log_path)],
        pipeline=pipeline,
        project="mall-service",
        package_prefix="com.demo",
        idle_debounce=0,
        seek_to_end=False,
    )

    processed = watcher.scan_once()

    assert processed == 1
    assert len(pipeline.calls) == 1
    assert pipeline.calls[0]["raw_text"] == "\n".join(
        [
            'java.lang.NullPointerException: Cannot invoke "User.getName()"',
            "at com.demo.service.UserService.getUserName(UserService.java:42)",
            "at com.demo.controller.UserController.getUser(UserController.java:28)",
            "Caused by: java.lang.IllegalStateException: user not found",
            "at com.demo.repository.UserRepository.findById(UserRepository.java:19)",
        ]
    )
    assert pipeline.calls[0]["source"] == f"log:{log_path}"
    assert pipeline.calls[0]["project"] == "mall-service"
    assert pipeline.calls[0]["title"] == "Auto detected: java.lang.NullPointerException"
    assert pipeline.calls[0]["package_prefix"] == "com.demo"
    assert str(pipeline.calls[0]["bug_id"]).startswith("log-")


def test_log_watcher_strips_exception_in_thread_prefix(tmp_path: Path) -> None:
    log_path = tmp_path / "app.log"
    log_path.write_text(
        'Exception in thread "main" java.lang.RuntimeException: boom\n'
        "    at com.demo.Main.main(Main.java:10)\n",
        encoding="utf-8",
    )
    pipeline = FakePipeline()
    watcher = LogWatcher([str(log_path)], pipeline=pipeline, idle_debounce=0, seek_to_end=False)

    assert watcher.scan_once() == 1
    assert pipeline.calls[0]["raw_text"] == "java.lang.RuntimeException: boom\nat com.demo.Main.main(Main.java:10)"


def test_log_watcher_starts_at_file_end_by_default(tmp_path: Path) -> None:
    log_path = tmp_path / "app.log"
    log_path.write_text(
        "java.lang.IllegalStateException: old\n"
        "    at com.demo.Old.fail(Old.java:1)\n",
        encoding="utf-8",
    )
    pipeline = FakePipeline()
    watcher = LogWatcher([str(log_path)], pipeline=pipeline, idle_debounce=0)

    assert watcher.scan_once() == 0
    assert pipeline.calls == []

    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(
            "java.lang.IllegalArgumentException: new\n"
            "    at com.demo.New.fail(New.java:2)\n"
        )

    assert watcher.scan_once() == 1
    assert "IllegalArgumentException" in str(pipeline.calls[0]["raw_text"])
    assert "IllegalStateException" not in str(pipeline.calls[0]["raw_text"])


def test_log_watcher_resets_offset_after_rotation(tmp_path: Path) -> None:
    log_path = tmp_path / "app.log"
    log_path.write_text("x" * 200, encoding="utf-8")
    pipeline = FakePipeline()
    watcher = LogWatcher(
        [str(log_path)],
        pipeline=pipeline,
        idle_debounce=0,
        seek_to_end=False,
    )

    assert watcher.scan_once() == 0

    log_path.write_text(
        "java.lang.RuntimeException: rotated\n"
        "    at com.demo.Rotated.fail(Rotated.java:7)\n",
        encoding="utf-8",
    )

    assert watcher.scan_once() == 1
    assert "RuntimeException" in str(pipeline.calls[0]["raw_text"])


def test_log_watcher_detects_rewritten_file_even_when_size_grows(tmp_path: Path) -> None:
    log_path = tmp_path / "app.log"
    log_path.write_text(
        "old header\n"
        "java.lang.IllegalStateException: old\n"
        "    at com.demo.Old.fail(Old.java:1)\n",
        encoding="utf-8",
    )
    pipeline = FakePipeline()
    watcher = LogWatcher([str(log_path)], pipeline=pipeline, idle_debounce=0)

    assert watcher.scan_once() == 0

    log_path.write_text(
        "new header with different prefix\n"
        "java.lang.IllegalArgumentException: new\n"
        "    at com.demo.New.fail(New.java:2)\n",
        encoding="utf-8",
    )

    assert watcher.scan_once() == 1
    assert "IllegalArgumentException" in str(pipeline.calls[0]["raw_text"])
    assert "IllegalStateException" not in str(pipeline.calls[0]["raw_text"])


def test_log_watcher_waits_for_frame_before_processing(tmp_path: Path) -> None:
    log_path = tmp_path / "app.log"
    log_path.write_text("java.lang.RuntimeException: half-written\n", encoding="utf-8")
    pipeline = FakePipeline()
    watcher = LogWatcher([str(log_path)], pipeline=pipeline, idle_debounce=0, seek_to_end=False)

    assert watcher.scan_once() == 0
    assert pipeline.calls == []

    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("    at com.demo.App.main(App.java:10)\n")

    assert watcher.scan_once() == 1
    assert pipeline.calls[0]["raw_text"] == "java.lang.RuntimeException: half-written\nat com.demo.App.main(App.java:10)"
