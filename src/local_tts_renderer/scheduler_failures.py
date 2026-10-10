from pathlib import Path

from .scheduler_types import ChapterJob, WorkerConfig


def summarize_job_failure(job_log: Path) -> str | None:
    if not job_log.exists():
        return None

    last_line: str | None = None
    with job_log.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if line:
                last_line = line
    if not last_line:
        return None
    return last_line[:240]


def print_final_job_failure(worker: WorkerConfig, job: ChapterJob, job_log: Path) -> None:
    summary = summarize_job_failure(job_log)
    if summary:
        print(f"[batch:error] worker={worker.name} chapter={job.chapter_index} detail={summary}", flush=True)
    print(f"[batch:error] worker={worker.name} chapter={job.chapter_index} log={job_log}", flush=True)


