from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_VOICE = "af_bella"
DEFAULT_OUTPUT_DIR = ".\\out"
DEFAULT_SPEED = 0.9
DEFAULT_MAX_CHARS = 1200
DEFAULT_MAX_PART_MINUTES = 30.0
WORKER_PROGRESS_INTERVAL_SECONDS = 3.0
WORKER_WAIT_LOG_INTERVAL_SECONDS = 15.0
CPU_IDLE_STEP_SECONDS = 120.0
DEFAULT_HEARTBEAT_SECONDS = 30.0
DEFAULT_WORKER_SILENCE_TIMEOUT_SECONDS = 300.0
DEFAULT_BOOTSTRAP_SILENCE_TIMEOUT_SECONDS = 90.0
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
