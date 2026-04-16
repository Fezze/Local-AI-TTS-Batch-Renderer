from __future__ import annotations

from pathlib import Path

from .model import SourceChapter, SourceDocument, SourceMetadata
from .registry_types import SourceLoadOptions
from ..input_parsers import split_markdown_chapters


SUPPORTED_SUFFIXES = frozenset({".md", ".markdown"})


def can_load(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_SUFFIXES


def load(path: Path, options: SourceLoadOptions | None = None) -> SourceDocument:
    options = options or SourceLoadOptions()
    raw_text = path.read_text(encoding="utf-8")
    chapters = split_markdown_chapters(
        raw_text,
        fallback_title=path.stem,
        single_chapter=options.markdown_single_chapter,
        max_chapter_chars=options.markdown_max_chapter_chars,
    )
    return SourceDocument(
        path=path,
        metadata=SourceMetadata(source_title=path.stem),
        chapters=[SourceChapter(title=chapter.title, text=chapter.text, group=chapter.group) for chapter in chapters],
    )


__all__ = ["SUPPORTED_SUFFIXES", "can_load", "load"]
