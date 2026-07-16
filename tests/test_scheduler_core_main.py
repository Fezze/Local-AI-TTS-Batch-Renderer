from __future__ import annotations

import shutil
import uuid
from argparse import Namespace
from pathlib import Path

from local_tts_renderer import scheduler_core as core
from local_tts_renderer.scheduler_types import ChapterJob


def _mk_tmp_dir() -> Path:
    path = Path.cwd() / ".test_tmp" / f"sched-core-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _base_args(output_dir: Path) -> Namespace:
    return Namespace(
        input=["dummy.md"],
        output_dir=str(output_dir),
        voice="voice_a",
        speed=1.0,
        max_chars=900,
        max_part_minutes=30.0,
        model_dir="models",
        silence_ms=0,
        force=False,
        keep_chunks=False,
        fresh=False,
        max_retries=1,
        cpu_max_chars=12000,
        cpu_worker_max_chars=900,
        gpu_large_chapter_max_chars=950,
        gpu_small_chapter_max_chars=1350,
        trim_mode="off",
        mp3_only=True,
        heartbeat_seconds=30.0,
        worker_silence_timeout_seconds=120.0,
        bootstrap_silence_timeout_seconds=45.0,
        gpu_short_first=False,
        gpu_workers=1,
        cpu_workers=1,
        providers=None,
        warmup_text="Warmup run.",
        gpu_recovery_seconds=10.0,
        aggressive_gpu_recovery=False,
        max_parts_per_run=0,
        no_console_controls=True,
        debug=False,
        serialize_gpu_bootstrap=True,
    )


def test_scheduler_main_no_inputs_returns_2(monkeypatch) -> None:
    args = _base_args(Path.cwd() / ".test_tmp" / "dummy")
    monkeypatch.setattr(core, "parse_args", lambda: args)
    monkeypatch.setattr(core, "expand_inputs", lambda _items: [])
    assert core.main() == 2


def test_scheduler_args_allow_disabling_mp3_only(monkeypatch) -> None:
    from local_tts_renderer import scheduler_args

    monkeypatch.setattr(
        "sys.argv",
        ["scheduler.py", "--input", "dummy.md", "--no-mp3-only"],
    )
    args = scheduler_args.parse_args()
    assert args.mp3_only is False


def test_scheduler_args_accept_out_alias(monkeypatch) -> None:
    from local_tts_renderer import scheduler_args

    monkeypatch.setattr(
        "sys.argv",
        ["scheduler.py", "--input", "dummy.md", "--out", "tmp-out"],
    )
    args = scheduler_args.parse_args()
    assert args.output_dir == "tmp-out"


def test_scheduler_main_happy_path(monkeypatch) -> None:
    tmp = _mk_tmp_dir()
    try:
        out = tmp / "out"
        out.mkdir(parents=True, exist_ok=True)
        source = tmp / "neutral.md"
        source.write_text("# Title\nBody", encoding="utf-8")
        args = _base_args(out)
        job = ChapterJob(
            source_path=source,
            chapter_index=1,
            chapter_title="Neutral Chapter",
            output_subdir="neutral",
            output_name="01-Neutral Chapter",
            estimated_chars=100,
            estimated_chunks=1,
        )

        monkeypatch.setattr(core, "parse_args", lambda: args)
        monkeypatch.setattr(core, "expand_inputs", lambda _items: [source])
        monkeypatch.setattr(core, "build_jobs", lambda *_a, **_k: ([job], [], {source: tmp / "cache.json"}))
        monkeypatch.setattr(core, "parse_provider_priority", lambda _p: ["CUDAExecutionProvider", "CPUExecutionProvider"])
        monkeypatch.setattr(core, "probe_available_providers", lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"])
        monkeypatch.setattr(core, "build_worker_provider_list", lambda **_k: ["CUDAExecutionProvider", "CPUExecutionProvider"])
        monkeypatch.setattr(core, "prepare_worker_temp_dirs", lambda workers: (tmp / "tmp-root", {w.name: tmp / "tmp-root" / w.name for w in workers}))
        logs: list[dict] = []
        monkeypatch.setattr(core, "append_runner_log", lambda _path, payload: logs.append(payload))
        monkeypatch.setattr(core, "start_console_controls", lambda **_k: (__import__("threading").Event(), None))

        def fake_run_worker(worker, pending_jobs, _args, _runner_log, *_rest):
            while pending_jobs:
                pending_jobs.pop(0)
                _rest[5]["done"] += 1  # counters

        monkeypatch.setattr(core, "run_worker", fake_run_worker)
        monkeypatch.setattr(core.shutil, "rmtree", lambda *_a, **_k: None)
        assert core.main() == 0
        assert logs[-1] == {
            "ts": logs[-1]["ts"],
            "event": "batch_finish",
            "inputs": 1,
            "chapter_jobs": 1,
            "done": 1,
            "failed": 0,
            "pending": 0,
            "exit_code": 0,
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_scheduler_main_returns_nonzero_for_failed_or_pending_jobs(monkeypatch) -> None:
    for outcome in ("failed", "pending"):
        tmp = _mk_tmp_dir()
        try:
            out = tmp / "out"
            out.mkdir(parents=True, exist_ok=True)
            source = tmp / "neutral.md"
            source.write_text("# Title\nBody", encoding="utf-8")
            args = _base_args(out)
            job = ChapterJob(
                source_path=source,
                chapter_index=1,
                chapter_title="Neutral Chapter",
                output_subdir="neutral",
                output_name="01-Neutral Chapter",
                estimated_chars=100,
                estimated_chunks=1,
            )
            logs: list[dict] = []

            monkeypatch.setattr(core, "parse_args", lambda: args)
            monkeypatch.setattr(core, "expand_inputs", lambda _items: [source])
            monkeypatch.setattr(core, "build_jobs", lambda *_a, **_k: ([job], [], {source: tmp / "cache.json"}))
            monkeypatch.setattr(core, "append_runner_log", lambda _path, payload: logs.append(payload))
            monkeypatch.setattr(core, "start_console_controls", lambda **_k: (__import__("threading").Event(), None))

            def fake_run_worker(worker, pending_jobs, _args, _runner_log, *_rest):
                if outcome == "failed":
                    try:
                        pending_jobs.pop(0)
                    except IndexError:
                        return
                    _rest[5]["failed"] += 1

            monkeypatch.setattr(core, "run_worker", fake_run_worker)
            monkeypatch.setattr(core.shutil, "rmtree", lambda *_a, **_k: None)

            assert core.main() == 1
            finish = logs[-1]
            assert finish["event"] == "batch_finish"
            assert finish["exit_code"] == 1
            assert finish["failed"] == (1 if outcome == "failed" else 0)
            assert finish["pending"] == (0 if outcome == "failed" else 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def test_scheduler_main_interrupt_reports_pending_and_returns_130(monkeypatch) -> None:
    tmp = _mk_tmp_dir()
    try:
        out = tmp / "out"
        out.mkdir(parents=True, exist_ok=True)
        source = tmp / "neutral.md"
        source.write_text("# Title\nBody", encoding="utf-8")
        args = _base_args(out)
        job = ChapterJob(
            source_path=source,
            chapter_index=1,
            chapter_title="Neutral Chapter",
            output_subdir="neutral",
            output_name="01-Neutral Chapter",
            estimated_chars=100,
            estimated_chunks=1,
        )
        logs: list[dict] = []
        terminated: list[bool] = []

        monkeypatch.setattr(core, "parse_args", lambda: args)
        monkeypatch.setattr(core, "expand_inputs", lambda _items: [source])
        monkeypatch.setattr(core, "build_jobs", lambda *_a, **_k: ([job], [], {source: tmp / "cache.json"}))
        monkeypatch.setattr(core, "append_runner_log", lambda _path, payload: logs.append(payload))
        monkeypatch.setattr(core, "start_console_controls", lambda **_k: (__import__("threading").Event(), None))
        monkeypatch.setattr(core, "terminate_all_active_processes", lambda force: terminated.append(force))
        monkeypatch.setattr(core.shutil, "rmtree", lambda *_a, **_k: None)

        class InterruptThread:
            def __init__(self, *args, **kwargs):
                pass

            def start(self) -> None:
                return None

            def join(self) -> None:
                raise KeyboardInterrupt

        monkeypatch.setattr(core.threading, "Thread", InterruptThread)

        assert core.main() == 130
        assert terminated == [True]
        interrupted = logs[-1]
        assert interrupted["event"] == "batch_interrupt"
        assert interrupted["done"] == 0
        assert interrupted["failed"] == 0
        assert interrupted["pending"] == 1
        assert interrupted["exit_code"] == 130
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
