from __future__ import annotations

import io
import shutil
import threading
import uuid
from argparse import Namespace
from pathlib import Path

from local_tts_renderer import scheduler_runtime as sr
from local_tts_renderer.scheduler_types import ChapterJob, WorkerConfig, WorkerStatus


def _mk_tmp_dir() -> Path:
    path = Path.cwd() / ".test_tmp" / f"scheduler-runtime-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _base_args(output_dir: Path) -> Namespace:
    return Namespace(
        output_dir=str(output_dir),
        fresh=False,
        trim_mode="off",
        force=False,
        keep_chunks=False,
        mp3_only=True,
        max_parts_per_run=0,
        cpu_max_chars=12000,
        gpu_short_first=False,
        debug=False,
        worker_silence_timeout_seconds=2.0,
        bootstrap_silence_timeout_seconds=1.0,
        serialize_gpu_bootstrap=True,
        gpu_recovery_seconds=1.0,
        aggressive_gpu_recovery=False,
        max_retries=0,
    )


class _DummyThread:
    def join(self, timeout=None):  # type: ignore[no-untyped-def]
        return None


def test_run_worker_success_flow(monkeypatch) -> None:
    tmp = _mk_tmp_dir()
    try:
        output_dir = tmp / "out"
        output_dir.mkdir(parents=True, exist_ok=True)
        source = tmp / "neutral.md"
        source.write_text("# T\nx", encoding="utf-8")
        job = ChapterJob(
            source_path=source,
            chapter_index=1,
            chapter_title="Neutral Chapter",
            output_subdir="neutral",
            output_name="01-Neutral Chapter",
            estimated_chars=100,
            estimated_chunks=1,
        )
        worker = WorkerConfig(name="cpu-1", provider="CPUExecutionProvider")
        args = _base_args(output_dir)
        pending_jobs = [job]
        statuses = {worker.name: WorkerStatus(idle_since=0.0)}
        counters = {"active": 0, "done": 0, "failed": 0, "completed_chunks": 0}
        condition = threading.Condition()
        logs: list[dict] = []

        monkeypatch.setattr(sr, "choose_worker_max_chars", lambda *a, **k: 900)
        monkeypatch.setattr(sr, "build_worker_command", lambda **k: ["python", "-u", "dummy.py"])
        monkeypatch.setattr(sr, "append_runner_log", lambda _p, payload: logs.append(payload))
        monkeypatch.setattr(sr, "print_batch_summary", lambda *a, **k: None)
        monkeypatch.setattr(sr, "clear_directory_contents", lambda *_a, **_k: None)
        monkeypatch.setattr(sr, "is_scheduler_paused", lambda: False)
        monkeypatch.setattr(sr, "debug_log", lambda *_a, **_k: None)
        monkeypatch.setattr(sr, "print_worker_progress", lambda *_a, **_k: None)
        monkeypatch.setattr(sr, "resolve_worker_silence_timeout", lambda *_a, **_k: 2.0)

        class FakeProcess:
            def __init__(self):
                self.stdout = io.StringIO(
                    '{"heartbeat": true, "chapter_title": "Neutral Chapter", "completed_chunks": 1, "total_chunks": 1}\n'
                    "[1/1] 100.0% chapter=1/1 chunk=1 chars=100 chunk_time=0.1s elapsed=0.1s eta=0.0s\n"
                )
                self.returncode = None
                self.pid = 999

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):  # type: ignore[no-untyped-def]
                self.returncode = 0
                return 0

        monkeypatch.setattr(sr.subprocess, "Popen", lambda *a, **k: FakeProcess())

        sr.run_worker(
            worker=worker,
            pending_jobs=pending_jobs,
            args=args,
            runner_log=output_dir / "runner.jsonl",
            python_exe=Path("python"),
            script_path=tmp / "md_to_audio.py",
            total_jobs=1,
            total_chunks=1,
            statuses=statuses,
            counters=counters,
            scheduler_condition=condition,
            batch_started_at=0.0,
            worker_temp_dirs={worker.name: tmp / "tmp-worker"},
            chapter_cache_map={source: tmp / "cache.json"},
            gpu_bootstrap_lock=threading.Lock(),
        )
        assert counters["done"] == 1
        assert any(entry.get("event") == "finish" for entry in logs)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_run_worker_timeout_path(monkeypatch) -> None:
    tmp = _mk_tmp_dir()
    try:
        output_dir = tmp / "out"
        output_dir.mkdir(parents=True, exist_ok=True)
        source = tmp / "neutral.md"
        source.write_text("# T\nx", encoding="utf-8")
        job = ChapterJob(
            source_path=source,
            chapter_index=2,
            chapter_title="Neutral Timeout",
            output_subdir="neutral",
            output_name="02-Neutral Timeout",
            estimated_chars=100,
            estimated_chunks=1,
            attempt=1,
        )
        worker = WorkerConfig(name="gpu-1", provider="CUDAExecutionProvider")
        args = _base_args(output_dir)
        args.worker_silence_timeout_seconds = 0.0
        args.bootstrap_silence_timeout_seconds = 0.0
        pending_jobs = [job]
        statuses = {worker.name: WorkerStatus(idle_since=0.0)}
        counters = {"active": 0, "done": 0, "failed": 0, "completed_chunks": 0}
        condition = threading.Condition()
        logs: list[dict] = []

        monkeypatch.setattr(sr, "choose_worker_max_chars", lambda *a, **k: 900)
        monkeypatch.setattr(sr, "build_worker_command", lambda **k: ["python", "-u", "dummy.py"])
        monkeypatch.setattr(sr, "append_runner_log", lambda _p, payload: logs.append(payload))
        monkeypatch.setattr(sr, "print_batch_summary", lambda *a, **k: None)
        monkeypatch.setattr(sr, "clear_directory_contents", lambda *_a, **_k: None)
        monkeypatch.setattr(sr, "is_scheduler_paused", lambda: False)
        monkeypatch.setattr(sr, "debug_log", lambda *_a, **_k: None)
        monkeypatch.setattr(sr, "resolve_worker_silence_timeout", lambda *_a, **_k: 0.0)
        monkeypatch.setattr(sr, "start_stdout_reader", lambda _s, _q: _DummyThread())
        monkeypatch.setattr(sr, "terminate_process_tree", lambda *_a, **_k: None)

        class QuietProcess:
            def __init__(self):
                self.stdout = io.StringIO("")
                self.returncode = None
                self.pid = 1001

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):  # type: ignore[no-untyped-def]
                self.returncode = -9
                return -9

        monkeypatch.setattr(sr.subprocess, "Popen", lambda *a, **k: QuietProcess())

        sr.run_worker(
            worker=worker,
            pending_jobs=pending_jobs,
            args=args,
            runner_log=output_dir / "runner.jsonl",
            python_exe=Path("python"),
            script_path=tmp / "md_to_audio.py",
            total_jobs=1,
            total_chunks=1,
            statuses=statuses,
            counters=counters,
            scheduler_condition=condition,
            batch_started_at=0.0,
            worker_temp_dirs={worker.name: tmp / "tmp-worker"},
            chapter_cache_map={source: tmp / "cache.json"},
            gpu_bootstrap_lock=threading.Lock(),
        )
        assert counters["failed"] == 1
        assert any(entry.get("event") == "timeout" for entry in logs)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

