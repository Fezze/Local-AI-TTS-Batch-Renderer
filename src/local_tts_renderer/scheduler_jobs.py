from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from .document_helpers import (
    build_group_directory_map,
    build_group_directory_map_from_navigation,
    sanitize_filename_component,
    slugify,
)
from .sources import MarkdownIngestOptions, SourceDocument, SourceLoadOptions, load_source

from .defaults import (
    DEFAULT_CPU_MAX_CHARS,
    DEFAULT_GPU_LARGE_CHAPTER_ESTIMATED_CHUNKS,
    DEFAULT_GPU_LARGE_CHAPTER_MIN_CHARS,
)
from .scheduler_logging import debug_log
from .scheduler_types import (
    CPU_IDLE_STEP_SECONDS,
    DEFAULT_MAX_CHARS,
    SHORT_SECTION_KEYWORDS,
    ChapterJob,
    WorkerConfig,
    WorkerStatus,
)


def _load_document_for_jobs(source_path: Path, md_single_chapter: bool, max_chapter_chars: int) -> SourceDocument:
    return load_source(
        source_path,
        SourceLoadOptions(
            markdown=MarkdownIngestOptions(
                single_chapter=md_single_chapter,
                max_chapter_chars=max_chapter_chars,
            )
        ),
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


def build_jobs(
    inputs: list[Path],
    output_dir: Path,
    fresh: bool,
    debug: bool = False,
    *,
    md_single_chapter: bool = False,
    max_chapter_chars: int = 0,
    max_chars: int = DEFAULT_MAX_CHARS,
    max_phoneme_chars: int = 0,
) -> tuple[list[ChapterJob], list[ChapterJob], dict[Path, Path]]:
    jobs: list[ChapterJob] = []
    skipped: list[ChapterJob] = []
    chapter_cache_map: dict[Path, Path] = {}
    cache_root = output_dir / ".cache" / "chapter-index"
    cache_root.mkdir(parents=True, exist_ok=True)
    for source_path in inputs:
        source_started = time.time()
        print(f"[batch:scan] source_start path={source_path}", flush=True)
        chapters_load_started = time.time()
        document = _load_document_for_jobs(source_path, md_single_chapter, max_chapter_chars)
        source_chapters = document.chapters
        chapters = [chapter for chapter in source_chapters if chapter.text and chapter.text.strip()]
        if chapters is not document.chapters:
            document = SourceDocument(path=document.path, metadata=document.metadata, chapters=chapters, navigation=document.navigation)
        cache_key = re_slug(str(source_path))
        cache_path = cache_root / f"{cache_key}.json"
        cache_payload = [
            {"title": chapter.title, "text": chapter.text, "group": chapter.group}
            for chapter in chapters
        ]
        cache_path.write_text(json.dumps(cache_payload, ensure_ascii=False), encoding="utf-8")
        chapter_cache_map[source_path] = cache_path
        print(
            f"[batch:scan] chapters_loaded path={source_path} chapters={len(chapters)} "
            f"elapsed={time.time() - chapters_load_started:.1f}s",
            flush=True,
        )
        group_dir_map = build_group_directory_map(document.chapters)
        root_title_index_map: dict[str, int] = {}
        used_output_names: dict[str, set[str]] = {}
        if document.navigation:
            toc_started = time.time()
            print(
                f"[batch:scan] navigation_loaded path={source_path} nodes={len(document.navigation)} "
                f"elapsed={time.time() - toc_started:.1f}s",
                flush=True,
            )
            group_dir_map = build_group_directory_map_from_navigation(
                document.navigation,
                {chapter.group for chapter in chapters if chapter.group},
            )
            for index, node in enumerate(document.navigation, start=1):
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
            effective_max_chars = max_chars
            if max_phoneme_chars > 0:
                effective_max_chars = min(effective_max_chars, max_phoneme_chars)
            estimated_chunks = max(1, (len(chapter.text) + effective_max_chars - 1) // effective_max_chars)
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
    return jobs, skipped, chapter_cache_map


def is_short_section_title(title: str) -> bool:
    normalized = title.strip().lower()
    return any(keyword in normalized for keyword in SHORT_SECTION_KEYWORDS)


def choose_worker_max_chars(worker: WorkerConfig, job: ChapterJob, args: argparse.Namespace) -> int:
    retry_base = 0.7 if getattr(args, "aggressive_gpu_recovery", False) else 0.8
    retry_shrink = retry_base ** max(job.attempt - 1, 0)
    if worker.provider == "CPUExecutionProvider":
        base = args.cpu_worker_max_chars
        if is_short_section_title(job.chapter_title):
            base = int(base * 1.2)
        return max(350, int(base * retry_shrink))
    if job.estimated_chunks >= DEFAULT_GPU_LARGE_CHAPTER_ESTIMATED_CHUNKS or job.estimated_chars >= DEFAULT_CPU_MAX_CHARS:
        base = args.gpu_large_chapter_max_chars
    else:
        base = args.gpu_small_chapter_max_chars
    return max(DEFAULT_GPU_LARGE_CHAPTER_MIN_CHARS, int(base * retry_shrink))


def build_worker_command(
    python_exe: Path,
    script_path: Path,
    args: argparse.Namespace,
    source_path: Path,
    job: ChapterJob,
    worker_max_chars: int,
    cache_path: Path | None,
) -> list[str]:
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
        "--warmup-text",
        args.warmup_text,
    ]
    if getattr(args, "max_phoneme_chars", 0) > 0:
        command.extend(["--max-phoneme-chars", str(args.max_phoneme_chars)])
    if cache_path is not None:
        command.extend(["--chapter-cache", str(cache_path)])
    if args.force:
        command.append("--force")
    if args.keep_chunks:
        command.append("--keep-chunks")
    if args.mp3_only:
        command.append("--mp3-only")
    if args.max_parts_per_run > 0:
        command.extend(["--max-parts-per-run", str(args.max_parts_per_run)])
    return command


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


def re_slug(value: str) -> str:
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
