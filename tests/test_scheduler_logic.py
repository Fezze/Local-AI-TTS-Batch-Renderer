from local_tts_renderer.scheduler import (
    ChapterJob,
    WorkerConfig,
    WorkerStatus,
    choose_worker_max_chars,
    cpu_allowed_chunk_budget,
    select_next_job,
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
