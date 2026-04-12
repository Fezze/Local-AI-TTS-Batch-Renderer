from __future__ import annotations

from dataclasses import dataclass

from .input_parsers import Chapter, TocNode


MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
DEFAULT_VOICE = "af_bella"
DEFAULT_LANG = "en-us"
DEFAULT_SPEED = 0.9
DEFAULT_MAX_CHARS = 850
DEFAULT_SILENCE_MS = 250
DEFAULT_MAX_PART_MINUTES = 30
GROUP_PATH_SEPARATOR = " / "
DEFAULT_TRIM_MODE = "off"
DEFAULT_HEARTBEAT_SECONDS = 30.0


class PartialRunComplete(Exception):
    pass


@dataclass
class Chunk:
    index: int
    heading: str | None
    text: str


@dataclass
class AudioMetadata:
    source_title: str
    author: str | None = None
    publisher: str | None = None
    published_date: str | None = None
    language: str | None = None

