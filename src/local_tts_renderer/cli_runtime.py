from __future__ import annotations

import argparse
import atexit
import ctypes
import json
import os
import tempfile
import threading
import time
from pathlib import Path

import requests

from .cli_models import (
    DEFAULT_HEARTBEAT_SECONDS,
    DEFAULT_LANG,
    DEFAULT_MAX_CHARS,
    DEFAULT_MAX_PART_MINUTES,
    DEFAULT_SILENCE_MS,
    DEFAULT_SPEED,
    DEFAULT_TRIM_MODE,
    DEFAULT_VOICE,
    MODEL_URL,
    VOICES_URL,
)
from .defaults import DEFAULT_MAX_PHONEME_CHARS, DEFAULT_MP3_ONLY, DEFAULT_OUTPUT_DIR, DEFAULT_WARMUP_TEXT
from .providers import resolve_provider

_ORT = None
_KOKORO_CLASS = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert English Markdown files to speech with Kokoro ONNX.")
    parser.add_argument("--input", nargs="+", help="Markdown file(s) to process.")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Directory for generated audio and manifests.")
    parser.add_argument("--model-dir", default="models", help="Directory for Kokoro model files.")
    parser.add_argument("--voice", default=DEFAULT_VOICE, help="Kokoro voice id, for example af_bella.")
    parser.add_argument("--lang", default=DEFAULT_LANG, help="Language code for Kokoro ONNX.")
    parser.add_argument("--speed", type=float, default=DEFAULT_SPEED, help="Speech speed multiplier.")
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help="Max text characters per chunk.")
    parser.add_argument("--max-phoneme-chars", type=int, default=DEFAULT_MAX_PHONEME_CHARS, help="Secondary chunk size cap to avoid phoneme truncation.")
    parser.add_argument("--silence-ms", type=int, default=DEFAULT_SILENCE_MS, help="Silence inserted between chunks.")
    parser.add_argument("--max-part-minutes", type=float, default=DEFAULT_MAX_PART_MINUTES, help="Maximum duration per output audio file.")
    parser.add_argument("--keep-chunks", action="store_true", help="Write one WAV file per chunk.")
    parser.add_argument(
        "--mp3-only",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_MP3_ONLY,
        help="Write only MP3 output files and skip WAV files on disk.",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing output files.")
    parser.add_argument("--wav-to-mp3", help="Convert an existing WAV file to MP3 without rerunning TTS.")
    parser.add_argument("--mp3-bitrate", type=int, default=192, help="MP3 bitrate in kbps for WAV to MP3 conversion.")
    parser.add_argument("--list-chapters", action="store_true", help="Print extracted chapter info and exit without generating audio.")
    parser.add_argument("--chapter-index", type=int, help="Render only one extracted chapter by 1-based index.")
    parser.add_argument("--chapter-cache", help="Optional JSON cache with pre-extracted chapters for faster chapter jobs.")
    parser.add_argument("--output-subdir", help="Optional output subdirectory under --output-dir for chapter batch jobs.")
    parser.add_argument("--output-name", help="Optional base output name for chapter batch jobs.")
    parser.add_argument("--md-single-chapter", action="store_true", help="Treat Markdown input as one chapter instead of splitting on headings.")
    parser.add_argument("--max-chapter-chars", type=int, default=0, help="Maximum character count per Markdown chapter. 0 disables extra splitting.")
    parser.add_argument("--trim-mode", choices=["full", "light", "off"], default=DEFAULT_TRIM_MODE, help="Silence trimming mode.")
    parser.add_argument("--heartbeat-seconds", type=float, default=DEFAULT_HEARTBEAT_SECONDS, help="Emit periodic heartbeat lines while rendering.")
    parser.add_argument("--providers", help="Comma-separated ONNX provider priority, for example CUDAExecutionProvider,CPUExecutionProvider.")
    parser.add_argument("--temp-dir", help="Optional temp directory used by runtime dependencies such as phonemizer.")
    parser.add_argument("--warmup-text", default=DEFAULT_WARMUP_TEXT, help="Optional short warmup text. Empty disables warmup.")
    parser.add_argument("--max-parts-per-run", type=int, default=0, help="Optional limit of closed parts per process run. 0 disables splitting.")
    return parser.parse_args()


def is_debug_enabled() -> bool:
    value = os.environ.get("LOCAL_TTS_DEBUG", "").strip().lower()
    return value in {"1", "true", "yes", "on", "debug"}


def debug_trace(message: str) -> None:
    if not is_debug_enabled():
        return
    print(f"[run:debug {time.strftime('%H:%M:%S')}] {message}", flush=True)


def configure_runtime_temp_dir(output_dir: Path, temp_dir: str | None = None) -> Path:
    preferred = temp_dir or os.environ.get("LOCAL_TTS_TEMP_DIR") or os.environ.get("TEMP") or os.environ.get("TMP")
    if preferred:
        root = Path(preferred).expanduser().resolve()
    else:
        root = Path(tempfile.gettempdir()).resolve() / "local-tts-runtime"
    root.mkdir(parents=True, exist_ok=True)
    resolved = str(root)
    os.environ["TMPDIR"] = resolved
    os.environ["TEMP"] = resolved
    os.environ["TMP"] = resolved
    tempfile.tempdir = resolved
    return root


def enable_windows_espeak_fallback() -> None:
    if os.name != "nt":
        return
    try:
        from phonemizer.backend.espeak import api as espeak_api
    except Exception:
        return

    if getattr(espeak_api.EspeakAPI, "_local_tts_patch_enabled", False):
        return

    original_init = espeak_api.EspeakAPI.__init__
    original_delete = espeak_api.EspeakAPI._delete

    def patched_init(self, library, data_path):
        try:
            return original_init(self, library, data_path)
        except PermissionError:
            encoded_data_path = None if data_path is None else str(data_path).encode("utf-8")
            try:
                espeak_lib = ctypes.cdll.LoadLibrary(str(library))
                library_path = espeak_api.EspeakAPI._shared_library_path(espeak_lib)
                del espeak_lib
            except OSError as error:
                raise RuntimeError(f"failed to load espeak library: {str(error)}") from None

            self._tempdir = tempfile.mkdtemp()
            atexit.register(self._delete_win32)
            self._library = ctypes.cdll.LoadLibrary(str(library_path))
            try:
                if self._library.espeak_Initialize(0x02, 0, encoded_data_path, 0) <= 0:
                    raise RuntimeError("failed to initialize espeak shared library")
            except AttributeError:
                raise RuntimeError("failed to load espeak library") from None

            self._library_path = library_path
            print(json.dumps({"espeak_copy_fallback": True, "library_path": str(library_path)}), flush=True)

    def patched_delete(library, tempdir):
        try:
            original_delete(library, tempdir)
        except Exception:
            return

    espeak_api.EspeakAPI.__init__ = patched_init
    espeak_api.EspeakAPI._delete = staticmethod(patched_delete)
    espeak_api.EspeakAPI._local_tts_patch_enabled = True


def start_progress_heartbeat(progress_state: dict, interval_seconds: float) -> tuple[threading.Event, threading.Thread | None]:
    stop_event = threading.Event()
    if interval_seconds <= 0:
        return stop_event, None

    def emit() -> None:
        while not stop_event.wait(interval_seconds):
            print(
                json.dumps(
                    {
                        "heartbeat": True,
                        "chapter_index": progress_state.get("chapter_index"),
                        "chapter_title": progress_state.get("chapter_title"),
                        "completed_chunks": progress_state.get("completed_chunks", 0),
                        "total_chunks": progress_state.get("total_chunks", 0),
                    }
                ),
                flush=True,
            )

    thread = threading.Thread(target=emit, name="tts-heartbeat", daemon=True)
    thread.start()
    return stop_event, thread


def ensure_file(path: Path, url: str) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=120) as response:
        response.raise_for_status()
        with path.open("wb") as handle:
            for part in response.iter_content(chunk_size=1024 * 1024):
                if part:
                    handle.write(part)


def ensure_model_files(model_dir: Path) -> tuple[Path, Path]:
    model_path = model_dir / "kokoro-v1.0.onnx"
    voices_path = model_dir / "voices-v1.0.bin"
    ensure_file(model_path, MODEL_URL)
    ensure_file(voices_path, VOICES_URL)
    return model_path, voices_path


def get_onnxruntime():
    global _ORT
    if _ORT is None:
        print("[run:bootstrap] loading onnxruntime...", flush=True)
        import onnxruntime as ort  # type: ignore

        _ORT = ort
    return _ORT


def get_kokoro_class():
    global _KOKORO_CLASS
    if _KOKORO_CLASS is None:
        print("[run:bootstrap] loading kokoro_onnx...", flush=True)
        from kokoro_onnx import Kokoro as KokoroClass  # type: ignore

        _KOKORO_CLASS = KokoroClass
    return _KOKORO_CLASS


def configure_onnx_provider(provider_priority: list[str] | None = None) -> str:
    ort = get_onnxruntime()
    available = ort.get_available_providers()
    requested = os.environ.get("ONNX_PROVIDER")
    requested_list = [requested] if requested else []
    resolution = resolve_provider(available=available, requested=requested_list, fallback=provider_priority)
    os.environ["ONNX_PROVIDER"] = resolution.selected
    return resolution.selected
