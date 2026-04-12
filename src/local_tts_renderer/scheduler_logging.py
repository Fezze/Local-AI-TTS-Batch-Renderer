from __future__ import annotations

import argparse
import json
import queue
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from .scheduler_types import WORKER_PROGRESS_INTERVAL_SECONDS, WorkerStatus


_LAST_BATCH_SUMMARY: tuple[int, int, int, int, int] | None = None
_LAST_WORKER_PROGRESS: dict[str, tuple[float, int, int]] = {}
PROGRESS_RE = re.compile(r"^\[(\d+)/(\d+)\]\s+([0-9.]+)% .* eta=([0-9.]+)s")


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def debug_log(enabled: bool, message: str) -> None:
    if not enabled:
        return
    print(f"[batch:debug {timestamp()}] {message}", flush=True)


def append_runner_log(log_path: Path, payload: dict) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def start_stdout_reader(stream, output_queue: "queue.Queue[str | None]") -> threading.Thread:
    def read_stream() -> None:
        try:
            for line in stream:
                output_queue.put(line)
        finally:
            output_queue.put(None)

    thread = threading.Thread(target=read_stream, name="tts-runner-stdout", daemon=True)
    thread.start()
    return thread


def parse_heartbeat_line(line: str) -> dict | None:
    stripped = line.strip()
    if not stripped.startswith("{"):
        return None
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return payload if payload.get("heartbeat") is True else None


def parse_worker_done_line(line: str) -> dict | None:
    stripped = line.strip()
    if not stripped.startswith("{"):
        return None
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return payload if payload.get("worker_job_done") is True else None


def update_worker_phase(current_phase: str, line: str) -> str:
    text = line.strip().lower()
    if "[run:bootstrap] loading onnxruntime..." in text:
        return "bootstrap_onnxruntime"
    if "[run:bootstrap] loading kokoro_onnx..." in text:
        return "bootstrap_model_load"
    if "[run:bootstrap] creating kokoro session..." in text:
        return "bootstrap_session"
    if "[run:warmup] start" in text:
        return "warmup"
    if "[run:warmup] done" in text:
        return "chapter_load"
    if text.startswith("[") and "/" in text and "chapter=" in text and "chunk=" in text:
        return "render"
    return current_phase


def is_bootstrap_phase(phase: str) -> bool:
    return phase.startswith("bootstrap") or phase == "warmup"


def resolve_worker_silence_timeout(args: argparse.Namespace, phase: str) -> float:
    worker_timeout = max(float(args.worker_silence_timeout_seconds), 1.0)
    if not is_bootstrap_phase(phase):
        return worker_timeout
    bootstrap_timeout = max(float(args.bootstrap_silence_timeout_seconds), 1.0)
    return min(worker_timeout, bootstrap_timeout)


def format_seconds(seconds: float) -> str:
    total = max(int(seconds), 0)
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m{secs:02d}s"


def print_batch_summary(
    statuses: dict[str, WorkerStatus],
    total_jobs: int,
    done_jobs: int,
    failed_jobs: int,
    completed_chunks: int,
    total_chunks: int,
    started_at: float,
) -> None:
    global _LAST_BATCH_SUMMARY
    running_jobs = sum(1 for status in statuses.values() if status.active)
    active_chunks = sum(status.progress_current for status in statuses.values() if status.active)
    done_chunk_equivalent = min(completed_chunks + active_chunks, total_chunks) if total_chunks else 0
    done_percent = (done_chunk_equivalent / total_chunks * 100.0) if total_chunks else 100.0
    elapsed_seconds = max(time.time() - started_at, 0.0)
    avg_chunk_seconds = (elapsed_seconds / done_chunk_equivalent) if done_chunk_equivalent else 0.0
    remaining_chunks = max(total_chunks - done_chunk_equivalent, 0)
    batch_eta_seconds = avg_chunk_seconds * remaining_chunks
    state = (done_jobs, running_jobs, failed_jobs, int(batch_eta_seconds), int(done_percent))
    if _LAST_BATCH_SUMMARY == state:
        return
    _LAST_BATCH_SUMMARY = state
    suffix = f" | eta {format_seconds(batch_eta_seconds)}" if running_jobs else ""
    print(f"[batch] done {done_jobs}/{total_jobs} | {done_percent:.1f}% | running {running_jobs} | failed {failed_jobs}{suffix}", flush=True)


def print_worker_progress(worker_name: str, chapter_title: str, line: str) -> None:
    match = PROGRESS_RE.match(line.strip())
    if not match:
        return
    current, total, percent, eta = match.groups()
    current_int = int(current)
    total_int = int(total)
    now = time.time()
    last = _LAST_WORKER_PROGRESS.get(worker_name)
    if last is not None:
        last_ts, last_current, last_total = last
        if total_int == last_total and current_int < total_int and (now - last_ts) < WORKER_PROGRESS_INTERVAL_SECONDS:
            return
        if total_int == last_total and current_int == last_current:
            return
    _LAST_WORKER_PROGRESS[worker_name] = (now, current_int, total_int)
    print(
        f"[{worker_name}] {chapter_title} | chunk {current}/{total} | {float(percent):.1f}% | eta {format_seconds(float(eta))}",
        flush=True,
    )
