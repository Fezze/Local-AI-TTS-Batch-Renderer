from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import time
from pathlib import Path

from .input_parsers import slugify

from .scheduler_jobs import (
    build_worker_command,
    choose_worker_max_chars,
    clear_directory_contents,
    re_slug,
    select_next_job,
)
from .scheduler_logging import (
    PROGRESS_RE,
    append_runner_log,
    debug_log,
    is_debug_enabled,
    parse_heartbeat_line,
    print_batch_summary,
    print_worker_progress,
    resolve_worker_silence_timeout,
    start_stdout_reader,
    timestamp,
    is_bootstrap_phase,
    update_worker_phase,
)
from .scheduler_process import (
    is_scheduler_paused,
    register_process,
    terminate_process_tree,
    unregister_process,
)
from .scheduler_types import ChapterJob, WorkerConfig, WorkerStatus, WORKER_WAIT_LOG_INTERVAL_SECONDS


def run_worker(
    worker: WorkerConfig,
    pending_jobs: list[ChapterJob],
    args: argparse.Namespace,
    runner_log: Path,
    python_exe: Path,
    script_path: Path,
    total_jobs: int,
    total_chunks: int,
    statuses: dict[str, WorkerStatus],
    counters: dict[str, int],
    scheduler_condition,
    batch_started_at: float,
    worker_temp_dirs: dict[str, Path],
    chapter_cache_map: dict[Path, Path],
    gpu_bootstrap_lock,
) -> None:
    while True:
        with scheduler_condition:
            while True:
                if is_scheduler_paused():
                    scheduler_condition.wait(timeout=0.5)
                    continue
                status = statuses[worker.name]
                now = time.time()
                if status.idle_since > now:
                    wait_seconds = status.idle_since - now
                    scheduler_condition.wait(timeout=max(wait_seconds, 0.01))
                    continue
                job_index = select_next_job(pending_jobs, worker, statuses, args.cpu_max_chars, args.gpu_short_first)
                if job_index is not None:
                    job = pending_jobs.pop(job_index)
                    counters["active"] += 1
                    debug_log(
                        args.debug,
                        f"worker_pick worker={worker.name} chapter={job.chapter_index} attempt={job.attempt} "
                        f"preferred={job.preferred_provider} pending_left={len(pending_jobs)}",
                    )
                    break
                if not statuses[worker.name].active and statuses[worker.name].idle_since == 0.0:
                    statuses[worker.name].idle_since = time.time()
                if counters["active"] == 0:
                    debug_log(args.debug, f"worker_exit worker={worker.name} reason=no_active_jobs")
                    return
                scheduler_condition.wait()

        source_path = job.source_path
        job_slug = re_slug(f"{source_path.stem}-{job.chapter_index:03d}-{job.chapter_title}")
        output_dir = Path(args.output_dir).resolve()
        source_output_dir = output_dir / slugify(source_path.stem)
        worker_max_chars = choose_worker_max_chars(worker, job, args)
        job_log = source_output_dir / f"{job_slug}.runner.log"
        resume_path = output_dir / job.output_subdir / f"{job.output_name}.resume.json"
        if args.fresh and resume_path.exists():
            resume_path.unlink()
        cache_path = chapter_cache_map.get(source_path)
        command = build_worker_command(
            python_exe=python_exe,
            script_path=script_path,
            args=args,
            source_path=source_path,
            job=job,
            worker_max_chars=worker_max_chars,
            cache_path=cache_path,
        )

        env = os.environ.copy()
        env["ONNX_PROVIDER"] = worker.provider
        env["PYTHONUTF8"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        worker_tmp = worker_temp_dirs[worker.name]
        clear_directory_contents(worker_tmp)
        worker_tmp.mkdir(parents=True, exist_ok=True)
        env["TMPDIR"] = str(worker_tmp)
        env["TEMP"] = str(worker_tmp)
        env["TMP"] = str(worker_tmp)
        env["LOCAL_TTS_TEMP_DIR"] = str(worker_tmp)
        if args.debug:
            env["LOCAL_TTS_DEBUG"] = "1"
        bootstrap_lock_acquired = False
        should_serialize_bootstrap = args.serialize_gpu_bootstrap and worker.provider != "CPUExecutionProvider"
        if should_serialize_bootstrap:
            print(f"[batch:bootstrap-lock] worker={worker.name} waiting", flush=True)
            gpu_bootstrap_lock.acquire()
            bootstrap_lock_acquired = True
            print(f"[batch:bootstrap-lock] worker={worker.name} acquired", flush=True)
        debug_log(
            args.debug,
            f"worker_spawn worker={worker.name} provider={worker.provider} temp={worker_tmp} "
            f"chapter={job.chapter_index} attempt={job.attempt}",
        )
        started_at = time.time()
        append_runner_log(
            runner_log,
            {
                "ts": timestamp(),
                "event": "start",
                "worker": worker.name,
                "provider": worker.provider,
                "input": str(source_path),
                "chapter_index": job.chapter_index,
                "chapter_title": job.chapter_title,
                "output_subdir": job.output_subdir,
                "output_name": job.output_name,
                "attempt": job.attempt,
                "log": str(job_log),
            },
        )
        with scheduler_condition:
            statuses[worker.name] = WorkerStatus(chapter_title=job.chapter_title, active=True, started_at=started_at, idle_since=0.0)
            print_batch_summary(statuses, total_jobs, counters["done"], counters["failed"], counters["completed_chunks"], total_chunks, batch_started_at)
        job_log.parent.mkdir(parents=True, exist_ok=True)
        process = None
        saw_cuda_error = False
        timed_out = False
        worker_phase = "spawn"
        try:
            with job_log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"ts": timestamp(), "worker": worker.name, "provider": worker.provider, "attempt": job.attempt, "max_chars": worker_max_chars, "trim_mode": args.trim_mode, "command": command}, ensure_ascii=False) + "\n")
                handle.flush()
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=env,
                    cwd=str(script_path.parent),
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    start_new_session=(os.name != "nt"),
                )
                register_process(worker.name, process)
                assert process.stdout is not None
                output_queue: queue.Queue[str | None] = queue.Queue()
                reader_thread = start_stdout_reader(process.stdout, output_queue)
                last_output_at = time.time()
                last_wait_log_at = 0.0
                while True:
                    try:
                        line = output_queue.get(timeout=1.0)
                    except queue.Empty:
                        if process.poll() is not None and output_queue.empty():
                            break
                        now = time.time()
                        idle_seconds = now - last_output_at
                        effective_timeout = resolve_worker_silence_timeout(args, worker_phase)
                        if (now - last_wait_log_at) >= WORKER_WAIT_LOG_INTERVAL_SECONDS:
                            print(
                                f"[batch:wait] worker={worker.name} chapter={job.chapter_index} phase={worker_phase} "
                                f"idle={idle_seconds:.1f}s timeout={effective_timeout:.1f}s",
                                flush=True,
                            )
                            if is_debug_enabled(args.debug):
                                debug_log(
                                    True,
                                    f"worker_waiting worker={worker.name} chapter={job.chapter_index} "
                                    f"phase={worker_phase} seconds_since_output={idle_seconds:.1f}",
                                )
                            last_wait_log_at = now
                        if idle_seconds >= effective_timeout:
                            timed_out = True
                            append_runner_log(
                                runner_log,
                                {
                                    "ts": timestamp(),
                                    "event": "timeout",
                                    "worker": worker.name,
                                    "provider": worker.provider,
                                    "input": str(source_path),
                                    "chapter_index": job.chapter_index,
                                    "chapter_title": job.chapter_title,
                                    "attempt": job.attempt,
                                    "phase": worker_phase,
                                    "idle_seconds": round(idle_seconds, 1),
                                    "timeout_seconds": effective_timeout,
                                    "log": str(job_log),
                                },
                            )
                            handle.write(
                                json.dumps(
                                    {
                                        "ts": timestamp(),
                                        "event": "timeout",
                                        "phase": worker_phase,
                                        "idle_seconds": round(idle_seconds, 1),
                                        "timeout_seconds": effective_timeout,
                                    },
                                    ensure_ascii=False,
                                )
                                + "\n"
                            )
                            handle.flush()
                            print(
                                f"[batch:timeout] worker={worker.name} chapter={job.chapter_index} phase={worker_phase} "
                                f"idle={idle_seconds:.1f}s limit={effective_timeout:.1f}s",
                                flush=True,
                            )
                            terminate_process_tree(process, force=False)
                            try:
                                process.wait(timeout=10)
                            except subprocess.TimeoutExpired:
                                terminate_process_tree(process, force=True)
                            debug_log(
                                args.debug,
                                f"worker_timeout worker={worker.name} chapter={job.chapter_index} attempt={job.attempt}",
                            )
                            break
                        continue
                    if line is None:
                        break
                    last_output_at = time.time()
                    worker_phase = update_worker_phase(worker_phase, line)
                    if bootstrap_lock_acquired and not is_bootstrap_phase(worker_phase):
                        gpu_bootstrap_lock.release()
                        bootstrap_lock_acquired = False
                        print(f"[batch:bootstrap-lock] worker={worker.name} released phase={worker_phase}", flush=True)
                    lowered = line.lower()
                    if (
                        "cudnn_status_execution_failed" in lowered
                        or "bad allocation" in lowered
                        or "cuda_call" in lowered
                        or ("onnxruntimeerror" in lowered and "cuda" in lowered)
                    ):
                        saw_cuda_error = True
                    handle.write(line)
                    handle.flush()
                    if is_debug_enabled(args.debug):
                        debug_log(True, f"worker_stdout worker={worker.name} line={line.strip()}")
                    heartbeat_payload = parse_heartbeat_line(line)
                    if heartbeat_payload is not None:
                        with scheduler_condition:
                            statuses[worker.name] = WorkerStatus(
                                chapter_title=heartbeat_payload.get("chapter_title") or job.chapter_title,
                                progress_current=int(heartbeat_payload.get("completed_chunks", 0)),
                                progress_total=int(heartbeat_payload.get("total_chunks", 0)),
                                percent=statuses[worker.name].percent,
                                eta_seconds=statuses[worker.name].eta_seconds,
                                active=True,
                                started_at=started_at,
                                idle_since=0.0,
                            )
                        continue
                    if PROGRESS_RE.match(line.strip()):
                        with scheduler_condition:
                            match = PROGRESS_RE.match(line.strip())
                            if match:
                                current, total, percent, eta = match.groups()
                                statuses[worker.name] = WorkerStatus(
                                    chapter_title=job.chapter_title,
                                    progress_current=int(current),
                                    progress_total=int(total),
                                    percent=float(percent),
                                    eta_seconds=float(eta),
                                    active=True,
                                    started_at=started_at,
                                    idle_since=0.0,
                                )
                                print_worker_progress(worker.name, job.chapter_title, line)
                reader_thread.join(timeout=1.0)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    terminate_process_tree(process, force=True)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
        except Exception as exc:
            append_runner_log(
                runner_log,
                {
                    "ts": timestamp(),
                    "event": "worker_exception",
                    "worker": worker.name,
                    "provider": worker.provider,
                    "input": str(source_path),
                    "chapter_index": job.chapter_index,
                    "chapter_title": job.chapter_title,
                    "attempt": job.attempt,
                    "error": repr(exc),
                    "log": str(job_log),
                },
            )
            print(
                f"[batch:worker-error] worker={worker.name} chapter={job.chapter_index} error={exc!r}",
                flush=True,
            )
        finally:
            if bootstrap_lock_acquired:
                gpu_bootstrap_lock.release()
                bootstrap_lock_acquired = False
                print(f"[batch:bootstrap-lock] worker={worker.name} released phase={worker_phase}", flush=True)
            unregister_process(worker.name)
        return_code = process.returncode if (process is not None and process.returncode is not None) else -9
        debug_log(
            args.debug,
            f"worker_finish worker={worker.name} chapter={job.chapter_index} attempt={job.attempt} "
            f"return_code={return_code} timed_out={timed_out} cuda_error={saw_cuda_error}",
        )
        append_runner_log(
            runner_log,
            {
                "ts": timestamp(),
                "event": "finish",
                "worker": worker.name,
                "provider": worker.provider,
                "input": str(source_path),
                "chapter_index": job.chapter_index,
                "chapter_title": job.chapter_title,
                "attempt": job.attempt,
                "log": str(job_log),
                "returncode": return_code,
                "elapsed_seconds": round(time.time() - started_at, 1),
            },
        )
        with scheduler_condition:
            counters["active"] -= 1
            if return_code == 0:
                counters["done"] += 1
                counters["completed_chunks"] += job.estimated_chunks
            elif return_code == 75 and args.max_parts_per_run > 0:
                pending_jobs.append(job)
                append_runner_log(
                    runner_log,
                    {
                        "ts": timestamp(),
                        "event": "partial_continue",
                        "worker": worker.name,
                        "input": str(source_path),
                        "chapter_index": job.chapter_index,
                        "chapter_title": job.chapter_title,
                        "attempt": job.attempt,
                        "log": str(job_log),
                    },
                )
                debug_log(
                    args.debug,
                    f"worker_partial_continue chapter={job.chapter_index} attempt={job.attempt}",
                )
            else:
                if job.attempt <= args.max_retries:
                    if job.fallback_locked:
                        counters["failed"] += 1
                        debug_log(args.debug, f"worker_failed_final chapter={job.chapter_index} reason=fallback_locked")
                        statuses[worker.name] = WorkerStatus(idle_since=time.time())
                        print_batch_summary(statuses, total_jobs, counters["done"], counters["failed"], counters["completed_chunks"], total_chunks, batch_started_at)
                        scheduler_condition.notify_all()
                        continue
                    retry_provider = job.preferred_provider
                    fallback_locked = False
                    if (saw_cuda_error or timed_out) and worker.provider != "CPUExecutionProvider":
                        retry_provider = "CPUExecutionProvider"
                        fallback_locked = True
                    retry_job = ChapterJob(
                        source_path=job.source_path,
                        chapter_index=job.chapter_index,
                        chapter_title=job.chapter_title,
                        output_subdir=job.output_subdir,
                        output_name=job.output_name,
                        estimated_chars=job.estimated_chars,
                        estimated_chunks=job.estimated_chunks,
                        attempt=job.attempt + 1,
                        preferred_provider=retry_provider,
                        fallback_locked=fallback_locked,
                    )
                    pending_jobs.append(retry_job)
                    append_runner_log(
                        runner_log,
                        {
                            "ts": timestamp(),
                            "event": "retry",
                            "worker": worker.name,
                            "input": str(source_path),
                            "chapter_index": job.chapter_index,
                            "chapter_title": job.chapter_title,
                            "next_attempt": retry_job.attempt,
                            "next_provider": retry_provider,
                            "timeout_triggered": timed_out,
                            "cuda_error_detected": saw_cuda_error,
                            "fallback_locked": fallback_locked,
                            "log": str(job_log),
                        },
                    )
                    debug_log(
                        args.debug,
                        f"worker_retry_enqueued chapter={job.chapter_index} next_attempt={retry_job.attempt} "
                        f"next_provider={retry_provider}",
                    )
                else:
                    counters["failed"] += 1
                    debug_log(args.debug, f"worker_failed_final chapter={job.chapter_index} attempts={job.attempt}")
            if (saw_cuda_error or timed_out) and worker.provider != "CPUExecutionProvider":
                recovery_seconds = max(args.gpu_recovery_seconds, 1.0)
                if args.aggressive_gpu_recovery:
                    recovery_seconds = max(recovery_seconds, 20.0)
                statuses[worker.name] = WorkerStatus(idle_since=time.time() + recovery_seconds)
                clear_directory_contents(worker_temp_dirs[worker.name])
                print(
                    f"[batch:recovery] worker={worker.name} provider={worker.provider} cooldown={recovery_seconds:.1f}s reason={'timeout' if timed_out else 'cuda_error'}",
                    flush=True,
                )
            else:
                statuses[worker.name] = WorkerStatus(idle_since=time.time())
            print_batch_summary(statuses, total_jobs, counters["done"], counters["failed"], counters["completed_chunks"], total_chunks, batch_started_at)
            scheduler_condition.notify_all()
