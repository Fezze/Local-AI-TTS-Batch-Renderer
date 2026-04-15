from __future__ import annotations

import argparse
from pathlib import Path

from .defaults import (
    DEFAULT_BOOTSTRAP_SILENCE_TIMEOUT_SECONDS,
    DEFAULT_AGGRESSIVE_GPU_RECOVERY,
    DEFAULT_CPU_MAX_CHARS,
    DEFAULT_CPU_WORKER_MAX_CHARS,
    DEFAULT_CPU_WORKERS,
    DEFAULT_DEBUG,
    DEFAULT_TRIM_MODE,
    DEFAULT_FORCE,
    DEFAULT_GPU_LARGE_CHAPTER_MAX_CHARS,
    DEFAULT_GPU_RECOVERY_SECONDS,
    DEFAULT_GPU_SMALL_CHAPTER_MAX_CHARS,
    DEFAULT_GPU_WORKERS,
    DEFAULT_HEARTBEAT_SECONDS,
    DEFAULT_KEEP_CHUNKS,
    DEFAULT_MAX_CHARS,
    DEFAULT_MAX_CHAPTER_CHARS,
    DEFAULT_MAX_PARTS_PER_RUN,
    DEFAULT_MAX_PHONEME_CHARS,
    DEFAULT_MAX_PART_MINUTES,
    DEFAULT_MAX_RETRIES,
    DEFAULT_MD_SINGLE_CHAPTER,
    DEFAULT_MP3_ONLY,
    DEFAULT_NO_CONSOLE_CONTROLS,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_PROVIDERS,
    DEFAULT_SERIALIZE_GPU_BOOTSTRAP,
    DEFAULT_SILENCE_MS,
    DEFAULT_SPEED,
    DEFAULT_VOICE,
    DEFAULT_WARMUP_TEXT,
    DEFAULT_WORKER_SILENCE_TIMEOUT_SECONDS,
)
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local TTS jobs with 2 GPU workers and 1 CPU worker.")
    parser.add_argument("--input", nargs="+", required=True, help="Input files, directories, or glob patterns.")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Directory for generated output.")
    parser.add_argument("--voice", default=DEFAULT_VOICE)
    parser.add_argument("--speed", type=float, default=DEFAULT_SPEED)
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    parser.add_argument("--max-phoneme-chars", type=int, default=DEFAULT_MAX_PHONEME_CHARS, help="Secondary chunk size cap to avoid phoneme truncation.")
    parser.add_argument("--max-part-minutes", type=float, default=DEFAULT_MAX_PART_MINUTES)
    parser.add_argument("--model-dir", default="models")
    parser.add_argument("--silence-ms", type=int, default=DEFAULT_SILENCE_MS)
    parser.add_argument("--force", action="store_true", default=DEFAULT_FORCE)
    parser.add_argument("--keep-chunks", action="store_true", default=DEFAULT_KEEP_CHUNKS)
    parser.add_argument("--fresh", action="store_true", default=DEFAULT_FORCE, help="Delete existing resume checkpoint for each input before starting.")
    parser.add_argument("--md-single-chapter", action="store_true", default=DEFAULT_MD_SINGLE_CHAPTER, help="Treat Markdown input as one chapter instead of splitting on headings.")
    parser.add_argument("--max-chapter-chars", type=int, default=DEFAULT_MAX_CHAPTER_CHARS, help="Maximum character count per Markdown chapter. 0 disables extra splitting.")
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES, help="Retry failed chapter jobs this many times.")
    parser.add_argument("--cpu-max-chars", type=int, default=DEFAULT_CPU_MAX_CHARS, help="CPU worker only takes jobs up to this estimated text size while GPUs are available.")
    parser.add_argument("--cpu-worker-max-chars", type=int, default=DEFAULT_CPU_WORKER_MAX_CHARS, help="Chunk size used by CPU worker jobs.")
    parser.add_argument("--gpu-large-chapter-max-chars", type=int, default=DEFAULT_GPU_LARGE_CHAPTER_MAX_CHARS, help="Chunk size used for larger chapters on GPU.")
    parser.add_argument("--gpu-small-chapter-max-chars", type=int, default=DEFAULT_GPU_SMALL_CHAPTER_MAX_CHARS, help="Chunk size used for smaller chapters on GPU.")
    parser.add_argument("--trim-mode", choices=["full", "light", "off"], default=DEFAULT_TRIM_MODE, help="Trimming mode passed to workers.")
    parser.add_argument(
        "--mp3-only",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_MP3_ONLY,
        help="Write only MP3 files from batch workers.",
    )
    parser.add_argument("--heartbeat-seconds", type=float, default=DEFAULT_HEARTBEAT_SECONDS, help="Worker heartbeat interval.")
    parser.add_argument("--worker-silence-timeout-seconds", type=float, default=DEFAULT_WORKER_SILENCE_TIMEOUT_SECONDS, help="Kill and retry a worker process if it produces no output for too long.")
    parser.add_argument(
        "--bootstrap-silence-timeout-seconds",
        type=float,
        default=DEFAULT_BOOTSTRAP_SILENCE_TIMEOUT_SECONDS,
        help="Stricter timeout while worker is in bootstrap/warmup phase.",
    )
    parser.add_argument("--gpu-short-first", action="store_true", help="For test runs, let GPU workers take the shortest remaining jobs first.")
    parser.add_argument("--gpu-workers", type=int, default=DEFAULT_GPU_WORKERS, help="Number of GPU workers.")
    parser.add_argument("--cpu-workers", type=int, default=DEFAULT_CPU_WORKERS, help="Number of CPU workers.")
    parser.add_argument("--providers", default=DEFAULT_PROVIDERS, help="Comma-separated provider priority, for example CUDAExecutionProvider,CPUExecutionProvider.")
    parser.add_argument("--warmup-text", default=DEFAULT_WARMUP_TEXT, help="Short warmup text passed to worker runtime initialization.")
    parser.add_argument("--gpu-recovery-seconds", type=float, default=DEFAULT_GPU_RECOVERY_SECONDS, help="Cooldown for GPU worker after CUDA/timeout failure to let VRAM recover.")
    parser.add_argument("--aggressive-gpu-recovery", action="store_true", default=DEFAULT_AGGRESSIVE_GPU_RECOVERY, help="Stronger GPU recovery strategy after CUDA/timeout failures.")
    parser.add_argument("--max-parts-per-run", type=int, default=DEFAULT_MAX_PARTS_PER_RUN, help="Optional: restart worker process after closing N parts (0 disables).")
    parser.add_argument("--no-console-controls", action="store_true", default=DEFAULT_NO_CONSOLE_CONTROLS, help="Disable keyboard controls (pause/restart) during batch run.")
    parser.add_argument("--debug", action="store_true", default=DEFAULT_DEBUG, help="Enable verbose batch debug logs.")
    parser.add_argument(
        "--serialize-gpu-bootstrap",
        dest="serialize_gpu_bootstrap",
        action="store_true",
        default=DEFAULT_SERIALIZE_GPU_BOOTSTRAP,
        help="Allow only one GPU worker to initialize ONNX Runtime at a time.",
    )
    parser.add_argument(
        "--no-serialize-gpu-bootstrap",
        dest="serialize_gpu_bootstrap",
        action="store_false",
        help="Disable serialized GPU bootstrap and allow parallel ONNX Runtime initialization.",
    )
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
