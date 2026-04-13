from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .defaults import (
    CPU_IDLE_STEP_SECONDS,
    DEFAULT_BOOTSTRAP_SILENCE_TIMEOUT_SECONDS,
    DEFAULT_HEARTBEAT_SECONDS,
    DEFAULT_MAX_CHARS,
    DEFAULT_MAX_PHONEME_CHARS,
    DEFAULT_MAX_PART_MINUTES,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_WORKER_SILENCE_TIMEOUT_SECONDS,
    DEFAULT_SPEED,
    DEFAULT_VOICE,
    SHORT_SECTION_KEYWORDS,
    WORKER_PROGRESS_INTERVAL_SECONDS,
    WORKER_WAIT_LOG_INTERVAL_SECONDS,
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
    fallback_locked: bool = False


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
