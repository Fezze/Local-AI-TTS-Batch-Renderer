from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path

from .scheduler_args import expand_inputs, parse_args
from .providers import build_worker_provider_list, parse_provider_priority, probe_available_providers
from .scheduler_jobs import (
    build_jobs,
    prepare_worker_temp_dirs,
)
from .scheduler_logging import append_runner_log, debug_log, timestamp
from .scheduler_process import (
    start_console_controls,
    terminate_all_active_processes,
)
from .scheduler_runtime import run_worker
from .scheduler_setup import log_scheduler_runtime, prepare_scheduler_runtime
from .scheduler_types import WorkerStatus


def main() -> int:
    args = parse_args()
    if args.debug:
        os.environ["LOCAL_TTS_DEBUG"] = "1"
    inputs = expand_inputs(args.input)
    print(f"[batch:init] inputs_resolved={len(inputs)} output_dir={Path(args.output_dir).resolve()}", flush=True)
    if not inputs:
        print("No input files found.")
        return 2
    output_dir = Path(args.output_dir).resolve()
    print(
        "[batch:config] "
        f"gpu_workers={args.gpu_workers} cpu_workers={args.cpu_workers} "
        f"max_retries={args.max_retries} silence_timeout={args.worker_silence_timeout_seconds}s "
        f"bootstrap_timeout={args.bootstrap_silence_timeout_seconds}s "
        f"trim_mode={args.trim_mode} mp3_only={args.mp3_only} warmup={'on' if bool(args.warmup_text.strip()) else 'off'} "
        f"max_parts_per_run={args.max_parts_per_run} "
        f"gpu_recovery={args.gpu_recovery_seconds}s aggressive_recovery={args.aggressive_gpu_recovery} "
        f"serialize_gpu_bootstrap={args.serialize_gpu_bootstrap} "
        f"console_controls={'off' if args.no_console_controls else 'on'}",
        flush=True,
    )
    chapter_jobs, skipped_jobs, chapter_cache_map = build_jobs(
        inputs,
        output_dir,
        args.fresh,
        debug=args.debug,
        md_single_chapter=getattr(args, "md_single_chapter", False),
        max_chapter_chars=getattr(args, "max_chapter_chars", 0),
        max_chars=args.max_chars,
        max_phoneme_chars=getattr(args, "max_phoneme_chars", 0),
    )
    if not chapter_jobs:
        if skipped_jobs:
            print(f"Nothing to do. Skipped {len(skipped_jobs)} completed chapter jobs.")
            return 0
        print("No chapter jobs found.")
        return 2
    print(f"[batch:plan] chapter_jobs={len(chapter_jobs)} skipped_completed={len(skipped_jobs)}", flush=True)

    runtime = prepare_scheduler_runtime(args, inputs, output_dir)
    workers = runtime.workers
    runner_log = runtime.runner_log
    run_tmp_root = runtime.run_tmp_root
    worker_temp_dirs = runtime.worker_temp_dirs
    log_scheduler_runtime(args, runtime)

    append_runner_log(
        runner_log,
        {
            "ts": timestamp(),
            "event": "batch_start",
            "inputs": [str(path) for path in inputs],
            "chapter_jobs": len(chapter_jobs),
            "skipped_completed_jobs": len(skipped_jobs),
            "workers": [worker.__dict__ for worker in workers],
        },
    )
    if skipped_jobs:
        print(f"[batch] skipped completed {len(skipped_jobs)}", flush=True)
    print(f"[batch] queued {len(chapter_jobs)} | skipped {len(skipped_jobs)}", flush=True)

    total_chunks = sum(job.estimated_chunks for job in chapter_jobs)
    now = time.time()
    statuses = {worker.name: WorkerStatus(idle_since=now) for worker in workers}
    counters = {"done": 0, "failed": 0, "active": 0, "completed_chunks": 0}
    scheduler_lock = threading.Lock()
    scheduler_condition = threading.Condition(scheduler_lock)
    controls_stop, controls_thread = start_console_controls(
        scheduler_condition=scheduler_condition,
        workers=workers,
        enabled=not args.no_console_controls,
    )
    pending_jobs = list(chapter_jobs)
    gpu_bootstrap_lock = threading.Lock()
    debug_log(args.debug, f"pending_jobs_initialized={len(pending_jobs)} total_chunks={total_chunks}")
    batch_started_at = time.time()
    threads = [
        threading.Thread(
            target=run_worker,
            args=(worker, pending_jobs, args, runner_log, runtime.python_exe, runtime.script_path, len(chapter_jobs), total_chunks, statuses, counters, scheduler_condition, batch_started_at, worker_temp_dirs, chapter_cache_map, gpu_bootstrap_lock),
            daemon=True,
        )
        for worker in workers
    ]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        terminate_all_active_processes(force=True)
        print("[batch] interrupted | terminated active worker processes", flush=True)
        append_runner_log(
            runner_log,
            {
                "ts": timestamp(),
                "event": "batch_interrupt",
                "inputs": len(inputs),
            },
        )
        return 130
    finally:
        controls_stop.set()
        if controls_thread is not None:
            controls_thread.join(timeout=1.0)
        shutil.rmtree(run_tmp_root, ignore_errors=True)

    print(f"[batch] finished | done {counters['done']}/{len(chapter_jobs)} | failed {counters['failed']}", flush=True)

    append_runner_log(
        runner_log,
        {
            "ts": timestamp(),
            "event": "batch_finish",
            "inputs": len(inputs),
            "chapter_jobs": len(chapter_jobs),
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
