from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from local_tts_renderer.input_parsers import (
    build_chapter_number_map,
    build_group_directory_map_from_toc,
    load_chapters,
    load_epub_toc_from_path,
    split_group_path,
    sanitize_filename_component,
    slugify,
)
from local_tts_renderer.providers import DEFAULT_PROVIDER_PRIORITY, build_worker_provider_list, parse_provider_priority


DEFAULT_VOICE = "af_bella"
DEFAULT_OUTPUT_DIR = ".\\out"
DEFAULT_SPEED = 0.9
DEFAULT_MAX_CHARS = 1200
DEFAULT_MAX_PART_MINUTES = 30.0
WORKER_PROGRESS_INTERVAL_SECONDS = 3.0
CPU_IDLE_STEP_SECONDS = 120.0
DEFAULT_HEARTBEAT_SECONDS = 30.0
DEFAULT_WORKER_SILENCE_TIMEOUT_SECONDS = 300.0
SHORT_SECTION_KEYWORDS = (
    "title page",
    "table of contents",
    "copyright",
    "acknowledg",
    "dedication",
    "map",
    "maps",
    "cover",
    "foreword",
    "afterword",
    "synopsis",
    "about the author",
    "about the publisher",
)


@dataclass
class WorkerConfig:
    name: str
    provider: str


@dataclass
class ChapterJob:
    source_path: Path
    chapter_index: int
    chapter_title: str
    output_subdir: str
    output_name: str
    estimated_chars: int
    estimated_chunks: int
    attempt: int = 1
    preferred_provider: str | None = None


@dataclass
class WorkerStatus:
    chapter_title: str = ""
    progress_current: int = 0
    progress_total: int = 0
    percent: float = 0.0
    eta_seconds: float = 0.0
    active: bool = False
    started_at: float = 0.0
    idle_since: float = 0.0


_LAST_BATCH_SUMMARY: tuple[int, int, int, int, int] | None = None
_LAST_WORKER_PROGRESS: dict[str, tuple[float, int, int]] = {}
_ACTIVE_PROCESSES: dict[str, subprocess.Popen] = {}
_ACTIVE_PROCESSES_LOCK = threading.Lock()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local TTS jobs with 2 GPU workers and 1 CPU worker.")
    parser.add_argument("--input", nargs="+", required=True, help="Input files, directories, or glob patterns.")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Directory for generated output.")
    parser.add_argument("--voice", default=DEFAULT_VOICE)
    parser.add_argument("--speed", type=float, default=DEFAULT_SPEED)
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    parser.add_argument("--max-part-minutes", type=float, default=DEFAULT_MAX_PART_MINUTES)
    parser.add_argument("--model-dir", default="models")
    parser.add_argument("--silence-ms", type=int, default=250)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--keep-chunks", action="store_true")
    parser.add_argument("--fresh", action="store_true", help="Delete existing resume checkpoint for each input before starting.")
    parser.add_argument("--max-retries", type=int, default=2, help="Retry failed chapter jobs this many times.")
    parser.add_argument("--cpu-max-chars", type=int, default=12000, help="CPU worker only takes jobs up to this estimated text size while GPUs are available.")
    parser.add_argument("--cpu-worker-max-chars", type=int, default=900, help="Chunk size used by CPU worker jobs.")
    parser.add_argument("--gpu-large-chapter-max-chars", type=int, default=950, help="Chunk size used for larger chapters on GPU.")
    parser.add_argument("--gpu-small-chapter-max-chars", type=int, default=1350, help="Chunk size used for smaller chapters on GPU.")
    parser.add_argument("--trim-mode", choices=["full", "light", "off"], default="off", help="Trimming mode passed to workers.")
    parser.add_argument("--mp3-only", action="store_true", default=True, help="Write only MP3 files from batch workers.")
    parser.add_argument("--heartbeat-seconds", type=float, default=DEFAULT_HEARTBEAT_SECONDS, help="Worker heartbeat interval.")
    parser.add_argument("--worker-silence-timeout-seconds", type=float, default=DEFAULT_WORKER_SILENCE_TIMEOUT_SECONDS, help="Kill and retry a worker process if it produces no output for too long.")
    parser.add_argument("--gpu-short-first", action="store_true", help="For test runs, let GPU workers take the shortest remaining jobs first.")
    parser.add_argument("--gpu-workers", type=int, default=2, help="Number of GPU workers.")
    parser.add_argument("--cpu-workers", type=int, default=1, help="Number of CPU workers.")
    parser.add_argument("--providers", help="Comma-separated provider priority, for example CUDAExecutionProvider,CPUExecutionProvider.")
    parser.add_argument("--debug", action="store_true", help="Enable verbose batch debug logs.")
    return parser.parse_args()


def expand_inputs(items: list[str]) -> list[Path]:
    expanded: list[Path] = []
    for item in items:
        item_path = Path(item)
        if item_path.exists() and item_path.is_dir():
            expanded.extend(sorted(path for path in item_path.iterdir() if path.is_file() and path.suffix.lower() in {".md", ".epub"}))
        elif any(ch in item for ch in "*?[]"):
            expanded.extend(sorted(Path().glob(item)))
        else:
            expanded.append(item_path)
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in expanded:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique


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


PROGRESS_RE = re.compile(r"^\[(\d+)/(\d+)\]\s+([0-9.]+)% .* eta=([0-9.]+)s")


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


def register_process(worker_name: str, process: subprocess.Popen) -> None:
    with _ACTIVE_PROCESSES_LOCK:
        _ACTIVE_PROCESSES[worker_name] = process


def unregister_process(worker_name: str) -> None:
    with _ACTIVE_PROCESSES_LOCK:
        _ACTIVE_PROCESSES.pop(worker_name, None)


def terminate_process_tree(process: subprocess.Popen, force: bool = False) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        flags = ["/T"]
        if force:
            flags.append("/F")
        subprocess.run(
            ["taskkill", *flags, "/PID", str(process.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return

    try:
        if force:
            os.killpg(process.pid, signal.SIGKILL)
        else:
            os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return


def terminate_all_active_processes(force: bool = True) -> None:
    with _ACTIVE_PROCESSES_LOCK:
        processes = list(_ACTIVE_PROCESSES.values())
    for process in processes:
        terminate_process_tree(process, force=force)


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


def cpu_allowed_chunk_budget(statuses: dict[str, WorkerStatus], worker_name: str) -> int:
    status = statuses.get(worker_name)
    if status is None:
        return 1
    idle_since = status.idle_since or time.time()
    idle_seconds = max(time.time() - idle_since, 0.0)
    return max(1, 1 + int(idle_seconds // CPU_IDLE_STEP_SECONDS))


def is_job_complete(output_dir: Path, job: ChapterJob) -> bool:
    candidate_manifest_paths = [
        (output_dir / job.output_subdir / job.output_name).with_suffix(".json"),
        (output_dir / job.output_subdir).with_suffix(".json"),
    ]
    manifest = None
    for manifest_path in candidate_manifest_paths:
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            break
        except Exception:
            continue
    if manifest is None:
        return False

    parts = manifest.get("parts")
    if not isinstance(parts, list) or not parts:
        return False

    for part in parts:
        wav_path = part.get("wav_path")
        mp3_path = part.get("mp3_path")
        if not mp3_path:
            return False
        mp3_file = Path(mp3_path)
        if not mp3_file.exists():
            return False
        if mp3_file.stat().st_size == 0:
            return False
        if wav_path:
            wav_file = Path(wav_path)
            if not wav_file.exists() or wav_file.stat().st_size == 0:
                return False

    return True


def build_jobs(inputs: list[Path], output_dir: Path, fresh: bool, debug: bool = False) -> tuple[list[ChapterJob], list[ChapterJob]]:
    jobs: list[ChapterJob] = []
    skipped: list[ChapterJob] = []
    for source_path in inputs:
        source_started = time.time()
        print(f"[batch:scan] source_start path={source_path}", flush=True)
        chapters_load_started = time.time()
        chapters = [chapter for chapter in load_chapters(source_path) if chapter.text and chapter.text.strip()]
        print(
            f"[batch:scan] chapters_loaded path={source_path} chapters={len(chapters)} "
            f"elapsed={time.time() - chapters_load_started:.1f}s",
            flush=True,
        )
        group_dir_map = {}
        root_title_index_map: dict[str, int] = {}
        used_output_names: dict[str, set[str]] = {}
        if source_path.suffix.lower() == ".epub":
            toc_started = time.time()
            toc_nodes = load_epub_toc_from_path(source_path)
            print(
                f"[batch:scan] toc_loaded path={source_path} nodes={len(toc_nodes)} "
                f"elapsed={time.time() - toc_started:.1f}s",
                flush=True,
            )
            group_dir_map = build_group_directory_map_from_toc(
                toc_nodes,
                {chapter.group for chapter in chapters if chapter.group},
            )
            for index, node in enumerate(toc_nodes, start=1):
                root_title_index_map.setdefault(sanitize_filename_component(node.title), index)
        source_slug = slugify(source_path.stem)
        local_counters: dict[str, int] = {}
        root_slot_counter = 0
        source_jobs_before = len(jobs)
        source_skipped_before = len(skipped)
        for chapter_index, chapter in enumerate(chapters, start=1):
            title_component = sanitize_filename_component(chapter.title)
            output_subdir = Path(source_slug)
            if chapter.group:
                output_subdir = output_subdir / group_dir_map.get(chapter.group, Path(sanitize_filename_component(chapter.group)))
                output_subdir_key = str(output_subdir)
                local_counters[output_subdir_key] = local_counters.get(output_subdir_key, 0) + 1
                chapter_number = local_counters[output_subdir_key]
            else:
                chapter_number = root_title_index_map.get(title_component)
                if chapter_number is None:
                    root_slot_counter += 1
                    chapter_number = root_slot_counter
            output_name = f"{chapter_number:02d}-{title_component}"
            subdir_key = str(output_subdir)
            used = used_output_names.setdefault(subdir_key, set())
            if output_name in used:
                output_name = f"{output_name}-c{chapter_index:03d}"
            used.add(output_name)
            debug_log(
                debug,
                f"job_candidate source={source_path.name} chapter_index={chapter_index} "
                f"subdir={output_subdir} output_name={output_name}",
            )
            estimated_chunks = max(1, (len(chapter.text) + DEFAULT_MAX_CHARS - 1) // DEFAULT_MAX_CHARS)
            job = ChapterJob(
                source_path=source_path,
                chapter_index=chapter_index,
                chapter_title=chapter.title,
                output_subdir=str(output_subdir),
                output_name=output_name,
                estimated_chars=len(chapter.text),
                estimated_chunks=estimated_chunks,
            )
            if not fresh and is_job_complete(output_dir, job):
                skipped.append(job)
            else:
                jobs.append(job)
        print(
            f"[batch:scan] source_done path={source_path} "
            f"queued={len(jobs) - source_jobs_before} skipped={len(skipped) - source_skipped_before} "
            f"elapsed={time.time() - source_started:.1f}s",
            flush=True,
        )
    return jobs, skipped


def is_short_section_title(title: str) -> bool:
    normalized = title.strip().lower()
    return any(keyword in normalized for keyword in SHORT_SECTION_KEYWORDS)


def choose_worker_max_chars(worker: WorkerConfig, job: ChapterJob, args: argparse.Namespace) -> int:
    if worker.provider == "CPUExecutionProvider":
        return args.cpu_worker_max_chars
    if job.estimated_chunks >= 12 or job.estimated_chars >= 12000:
        return args.gpu_large_chapter_max_chars
    return args.gpu_small_chapter_max_chars


def select_next_job(
    pending_jobs: list[ChapterJob],
    worker: WorkerConfig,
    statuses: dict[str, WorkerStatus],
    cpu_max_chars: int,
    gpu_short_first: bool,
) -> int | None:
    if not pending_jobs:
        return None

    def job_key(job: ChapterJob) -> tuple[int, int, int]:
        return (job.estimated_chunks, job.estimated_chars, job.chapter_index)

    if worker.provider == "CPUExecutionProvider":
        preferred_cpu = [
            (index, job)
            for index, job in enumerate(pending_jobs)
            if job.preferred_provider == "CPUExecutionProvider"
        ]
        if preferred_cpu:
            return min(preferred_cpu, key=lambda item: job_key(item[1]))[0]

        allowed_chunks = cpu_allowed_chunk_budget(statuses, worker.name)
        eligible = [
            (index, job)
            for index, job in enumerate(pending_jobs)
            if job.preferred_provider in (None, "CPUExecutionProvider")
            and (is_short_section_title(job.chapter_title) or (job.estimated_chars <= cpu_max_chars and job.estimated_chunks <= allowed_chunks))
        ]
        if eligible:
            return min(eligible, key=lambda item: job_key(item[1]))[0]

        gpu_active = any(
            name.startswith("gpu-") and status.active
            for name, status in statuses.items()
        )
        if gpu_active:
            return None
        relaxed_eligible = [
            index
            for index, job in enumerate(pending_jobs)
            if job.preferred_provider in (None, "CPUExecutionProvider")
            and job.estimated_chunks <= allowed_chunks
            and job.estimated_chars <= cpu_max_chars
        ]
        if relaxed_eligible:
            return min(relaxed_eligible, key=lambda index: job_key(pending_jobs[index]))
        return min(range(len(pending_jobs)), key=lambda index: job_key(pending_jobs[index]))

    gpu_eligible = [
        index
        for index, job in enumerate(pending_jobs)
        if job.preferred_provider in (None, worker.provider)
    ]
    if not gpu_eligible:
        return None
    if gpu_short_first:
        return min(gpu_eligible, key=lambda index: job_key(pending_jobs[index]))
    return max(gpu_eligible, key=lambda index: job_key(pending_jobs[index]))


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
    scheduler_condition: threading.Condition,
    batch_started_at: float,
    worker_temp_dirs: dict[str, Path],
) -> None:
    debug_log(args.debug, f"worker_loop_start worker={worker.name} provider={worker.provider}")
    while True:
        with scheduler_condition:
            while True:
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
        command = [
            str(python_exe),
            "-u",
            str(script_path),
            "--input",
            str(source_path),
            "--chapter-index",
            str(job.chapter_index),
            "--output-subdir",
            job.output_subdir,
            "--output-name",
            job.output_name,
            "--output-dir",
            str(Path(args.output_dir).resolve()),
            "--voice",
            args.voice,
            "--speed",
            str(args.speed),
            "--max-chars",
            str(worker_max_chars),
            "--max-part-minutes",
            str(args.max_part_minutes),
            "--model-dir",
            str(Path(args.model_dir).resolve()),
            "--silence-ms",
            str(args.silence_ms),
            "--trim-mode",
            args.trim_mode,
            "--heartbeat-seconds",
            str(args.heartbeat_seconds),
        ]
        if args.force:
            command.append("--force")
        if args.keep_chunks:
            command.append("--keep-chunks")
        if args.mp3_only:
            command.append("--mp3-only")

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
            saw_cuda_error = False
            try:
                assert process.stdout is not None
                output_queue: queue.Queue[str | None] = queue.Queue()
                reader_thread = start_stdout_reader(process.stdout, output_queue)
                last_output_at = time.time()
                last_wait_debug_at = 0.0
                timed_out = False
                while True:
                    try:
                        line = output_queue.get(timeout=1.0)
                    except queue.Empty:
                        if process.poll() is not None and output_queue.empty():
                            break
                        now = time.time()
                        if args.debug and (now - last_wait_debug_at) >= 10.0:
                            debug_log(
                                True,
                                f"worker_waiting worker={worker.name} chapter={job.chapter_index} "
                                f"seconds_since_output={now - last_output_at:.1f}",
                            )
                            last_wait_debug_at = now
                        if (time.time() - last_output_at) >= args.worker_silence_timeout_seconds:
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
                                    "timeout_seconds": args.worker_silence_timeout_seconds,
                                    "log": str(job_log),
                                },
                            )
                            handle.write(json.dumps({"ts": timestamp(), "event": "timeout", "timeout_seconds": args.worker_silence_timeout_seconds}, ensure_ascii=False) + "\n")
                            handle.flush()
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
                    if args.debug:
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
            finally:
                unregister_process(worker.name)
        return_code = process.returncode if process.returncode is not None else -9
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
            else:
                if job.attempt <= args.max_retries:
                    retry_provider = job.preferred_provider
                    if (saw_cuda_error or timed_out) and worker.provider != "CPUExecutionProvider":
                        retry_provider = "CPUExecutionProvider"
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
            statuses[worker.name] = WorkerStatus(idle_since=time.time())
            print_batch_summary(statuses, total_jobs, counters["done"], counters["failed"], counters["completed_chunks"], total_chunks, batch_started_at)
            scheduler_condition.notify_all()


def re_slug(value: str) -> str:
    import re

    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip()).strip("-").lower()
    return slug or "document"


def clear_directory_contents(path: Path) -> None:
    if not path.exists():
        return
    for entry in path.iterdir():
        try:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
        except Exception:
            continue


def prepare_worker_temp_dirs(workers: list[WorkerConfig]) -> tuple[Path, dict[str, Path]]:
    run_tmp_root = Path(tempfile.gettempdir()).resolve() / "local-tts-batch" / f"pid-{os.getpid()}"
    run_tmp_root.mkdir(parents=True, exist_ok=True)
    worker_dirs: dict[str, Path] = {}
    for worker in workers:
        worker_dir = run_tmp_root / worker.name
        worker_dir.mkdir(parents=True, exist_ok=True)
        worker_dirs[worker.name] = worker_dir
    return run_tmp_root, worker_dirs


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
        f"trim_mode={args.trim_mode} mp3_only={args.mp3_only}",
        flush=True,
    )
    chapter_jobs, skipped_jobs = build_jobs(inputs, output_dir, args.fresh, debug=args.debug)
    if not chapter_jobs:
        if skipped_jobs:
            print(f"Nothing to do. Skipped {len(skipped_jobs)} completed chapter jobs.")
            return 0
        print("No chapter jobs found.")
        return 2
    print(f"[batch:plan] chapter_jobs={len(chapter_jobs)} skipped_completed={len(skipped_jobs)}", flush=True)

    python_exe = Path(sys.executable).resolve()
    script_path = Path(__file__).resolve().parents[2] / "md_to_audio.py"
    runner_log = (output_dir / slugify(inputs[0].stem) / "runner.jsonl") if len(inputs) == 1 else (output_dir / "runner.jsonl")
    provider_priority = parse_provider_priority(args.providers)
    available_providers = list(dict.fromkeys([*provider_priority, "CPUExecutionProvider"]))
    print(
        f"[batch:providers] available_probe=skipped priority={provider_priority}",
        flush=True,
    )
    debug_log(args.debug, f"provider_probe_fallback_available={available_providers}")
    worker_providers = build_worker_provider_list(
        available=available_providers,
        gpu_workers=args.gpu_workers,
        cpu_workers=args.cpu_workers,
        provider_priority=provider_priority,
    )
    workers: list[WorkerConfig] = []
    gpu_index = 0
    cpu_index = 0
    for provider in worker_providers:
        if provider == "CPUExecutionProvider":
            cpu_index += 1
            workers.append(WorkerConfig(name=f"cpu-{cpu_index}", provider=provider))
        else:
            gpu_index += 1
            workers.append(WorkerConfig(name=f"gpu-{gpu_index}", provider=provider))
    run_tmp_root, worker_temp_dirs = prepare_worker_temp_dirs(workers)
    print(f"[batch:workers] {', '.join(f'{w.name}:{w.provider}' for w in workers)}", flush=True)
    print(f"[batch:runtime] runner_log={runner_log} tmp_root={run_tmp_root}", flush=True)
    debug_log(args.debug, f"python_exe={python_exe} script_path={script_path}")
    debug_log(args.debug, f"provider_order_resolved={worker_providers}")

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
    pending_jobs = list(chapter_jobs)
    debug_log(args.debug, f"pending_jobs_initialized={len(pending_jobs)} total_chunks={total_chunks}")
    batch_started_at = time.time()
    threads = [
        threading.Thread(
            target=run_worker,
            args=(worker, pending_jobs, args, runner_log, python_exe, script_path, len(chapter_jobs), total_chunks, statuses, counters, scheduler_condition, batch_started_at, worker_temp_dirs),
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
