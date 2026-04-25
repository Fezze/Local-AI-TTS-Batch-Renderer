from local_tts_renderer.scheduler import (
    ChapterJob,
    WorkerConfig,
    WorkerStatus,
    build_worker_command,
    choose_worker_max_chars,
    cpu_allowed_chunk_budget,
    parse_args,
    resolve_worker_silence_timeout,
    select_next_job,
    update_worker_phase,
)


def _job(index: int, title: str, chars: int, chunks: int) -> ChapterJob:
    from pathlib import Path

    return ChapterJob(
        source_path=Path("book.md"),
        chapter_index=index,
        chapter_title=title,
        output_subdir="book",
        output_name=f"{index:02d}-{title}",
        estimated_chars=chars,
        estimated_chunks=chunks,
    )


def test_cpu_worker_prefers_short_sections() -> None:
    jobs = [_job(1, "Chapter 1", 5000, 5), _job(2, "Table of Contents", 9000, 9)]
    statuses = {"cpu-1": WorkerStatus(idle_since=0)}
    index = select_next_job(jobs, WorkerConfig(name="cpu-1", provider="CPUExecutionProvider"), statuses, cpu_max_chars=12000, gpu_short_first=False)
    assert index == 1


def test_gpu_worker_prefers_largest_job() -> None:
    jobs = [_job(1, "Small", 1000, 2), _job(2, "Big", 10000, 10)]
    statuses = {"gpu-1": WorkerStatus(idle_since=0)}
    index = select_next_job(jobs, WorkerConfig(name="gpu-1", provider="CUDAExecutionProvider"), statuses, cpu_max_chars=12000, gpu_short_first=False)
    assert index == 1


def test_cpu_idle_budget_increases() -> None:
    statuses = {"cpu-1": WorkerStatus(idle_since=1)}
    budget = cpu_allowed_chunk_budget(statuses, "cpu-1")
    assert budget >= 1


def test_max_chars_shrinks_on_retry_for_gpu() -> None:
    class Args:
        cpu_worker_max_chars = 900
        gpu_large_chapter_max_chars = 950
        gpu_small_chapter_max_chars = 1350

    job_first = _job(1, "Big", 15000, 15)
    job_retry = ChapterJob(**{**job_first.__dict__, "attempt": 2})
    first = choose_worker_max_chars(WorkerConfig(name="gpu-1", provider="CUDAExecutionProvider"), job_first, Args())
    retry = choose_worker_max_chars(WorkerConfig(name="gpu-1", provider="CUDAExecutionProvider"), job_retry, Args())
    assert retry < first


def test_worker_command_includes_max_parts_per_run_flag() -> None:
    from argparse import Namespace
    from pathlib import Path

    args = Namespace(
        output_dir="out",
        voice="voice_a",
        speed=1.0,
        max_part_minutes=30.0,
        model_dir="models",
        silence_ms=250,
        trim_mode="off",
        heartbeat_seconds=30.0,
        warmup_text="Warmup run.",
        force=False,
        keep_chunks=False,
        mp3_only=True,
        max_parts_per_run=1,
    )
    job = _job(4, "Section Alpha", 12000, 12)
    command = build_worker_command(
        python_exe=Path("python"),
        script_path=Path("md_to_audio.py"),
        args=args,
        source_path=Path("doc.epub"),
        job=job,
        worker_max_chars=900,
        cache_path=Path("cache.json"),
    )
    assert "--max-parts-per-run" in command
    assert "1" in command


def test_worker_command_omits_max_parts_per_run_when_disabled() -> None:
    from argparse import Namespace
    from pathlib import Path

    args = Namespace(
        output_dir="out",
        voice="voice_a",
        speed=1.0,
        max_part_minutes=30.0,
        model_dir="models",
        silence_ms=250,
        trim_mode="off",
        heartbeat_seconds=30.0,
        warmup_text="Warmup run.",
        force=False,
        keep_chunks=False,
        mp3_only=True,
        max_parts_per_run=0,
    )
    job = _job(4, "Section Alpha", 12000, 12)
    command = build_worker_command(
        python_exe=Path("python"),
        script_path=Path("md_to_audio.py"),
        args=args,
        source_path=Path("doc.epub"),
        job=job,
        worker_max_chars=900,
        cache_path=None,
    )
    assert "--max-parts-per-run" not in command


def test_update_worker_phase_detects_bootstrap_and_render() -> None:
    phase = "spawn"
    phase = update_worker_phase(phase, "[run:bootstrap] loading onnxruntime...")
    assert phase == "bootstrap_onnxruntime"
    phase = update_worker_phase(phase, "[run:warmup] start")
    assert phase == "warmup"
    phase = update_worker_phase(phase, "[run:warmup] done elapsed=0.4s")
    assert phase == "chapter_load"
    phase = update_worker_phase(phase, "[4/10] 40.0% chapter=1/1 chunk=4 chars=500 chunk_time=2.1s elapsed=10.0s eta=15.0s")
    assert phase == "render"


def test_resolve_worker_silence_timeout_uses_bootstrap_limit() -> None:
    from argparse import Namespace

    args = Namespace(worker_silence_timeout_seconds=180.0, bootstrap_silence_timeout_seconds=45.0)
    assert resolve_worker_silence_timeout(args, "bootstrap_session") == 45.0
    assert resolve_worker_silence_timeout(args, "warmup") == 45.0
    assert resolve_worker_silence_timeout(args, "render") == 180.0


def test_parse_args_disables_gpu_bootstrap_serialization_when_requested(monkeypatch) -> None:
    import sys

    monkeypatch.setattr(
        sys,
        "argv",
        ["run_tts_batch.py", "--input", "book.epub", "--no-serialize-gpu-bootstrap", "--md-chapter-heading-level", "2"],
    )
    args = parse_args()
    assert args.serialize_gpu_bootstrap is False
    assert args.md_chapter_heading_level == 2
