from __future__ import annotations

from dataclasses import dataclass

from .defaults import (
    DEFAULT_HEARTBEAT_SECONDS,
    DEFAULT_LANG,
    DEFAULT_MAX_CHARS,
    DEFAULT_MAX_PART_MINUTES,
    DEFAULT_SILENCE_MS,
    DEFAULT_SPEED,
    DEFAULT_TRIM_MODE,
    DEFAULT_VOICE,
    GROUP_PATH_SEPARATOR,
    MODEL_URL,
    VOICES_URL,
)
from .input_parsers import Chapter, TocNode


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
