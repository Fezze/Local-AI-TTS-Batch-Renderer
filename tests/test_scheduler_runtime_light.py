from __future__ import annotations

import threading
from argparse import Namespace
from pathlib import Path

from local_tts_renderer.scheduler_runtime import run_worker
from local_tts_renderer.scheduler_types import WorkerConfig, WorkerStatus


def test_run_worker_exits_when_no_jobs() -> None:
    worker = WorkerConfig(name="cpu-1", provider="CPUExecutionProvider")
    pending_jobs = []
    args = Namespace(cpu_max_chars=12000, gpu_short_first=False, debug=False)
    runner_log = Path("runner.jsonl")
    statuses = {"cpu-1": WorkerStatus(idle_since=0.0)}
    counters = {"active": 0, "done": 0, "failed": 0, "completed_chunks": 0}
    condition = threading.Condition()
    run_worker(
        worker=worker,
        pending_jobs=pending_jobs,
        args=args,
        runner_log=runner_log,
        python_exe=Path("python"),
        script_path=Path("md_to_audio.py"),
        total_jobs=0,
        total_chunks=0,
        statuses=statuses,
        counters=counters,
        scheduler_condition=condition,
        batch_started_at=0.0,
        worker_temp_dirs={worker.name: Path(".")},
        chapter_cache_map={},
        gpu_bootstrap_lock=threading.Lock(),
    )
    assert counters["done"] == 0

