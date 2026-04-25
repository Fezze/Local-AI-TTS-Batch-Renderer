from __future__ import annotations

"""Backward-compatible parser facade.

Internal code should use `local_tts_renderer.sources`, `sources.model`, and
`document_helpers` directly. Keep this module thin; do not add new ingestion
logic here.
"""

from pathlib import Path

from .document_helpers import (
    build_chapter_number_map,
    build_group_directory_map,
    clean_plain_text,
    get_group_leaf_title,
    join_group_path,
    sanitize_filename_component,
    slugify,
    split_group_path,
)
from .sources import SourceLoadOptions, load_source
from .sources.epub import TocNode, build_toc_lookup, extract_epub_chapters_dynamic, load_epub_toc_from_path
from .sources.epub import extract_epub_metadata as _extract_source_epub_metadata
from .sources.markdown import clean_markdown, split_markdown_chapters, strip_front_matter
from .sources.model import SourceChapter as Chapter


def build_group_directory_map_from_toc(nodes: list[TocNode], selected_groups: set[str]) -> dict[str, Path]:
    from .document_helpers import build_group_directory_map_from_navigation
    from .sources.epub import _navigation_from_toc

    return build_group_directory_map_from_navigation(_navigation_from_toc(nodes), selected_groups)


def load_chapters(
    source_path: Path,
    *,
    single_chapter: bool = False,
    max_chapter_chars: int = 0,
    chapter_heading_level: int = 0,
) -> list[Chapter]:
    document = load_source(
        source_path,
        SourceLoadOptions(
            markdown_single_chapter=single_chapter,
            markdown_max_chapter_chars=max_chapter_chars,
            markdown_chapter_heading_level=chapter_heading_level,
        ),
    )
    return document.chapters


def extract_epub_metadata(path: Path):
    from .cli_models import AudioMetadata

    source_metadata = _extract_source_epub_metadata(path)
    return AudioMetadata(
        source_title=source_metadata.source_title,
        author=source_metadata.author,
        publisher=source_metadata.publisher,
        published_date=source_metadata.published_date,
        language=source_metadata.language,
    )


__all__ = [
    "Chapter",
    "TocNode",
    "build_chapter_number_map",
    "build_group_directory_map",
    "build_group_directory_map_from_toc",
    "build_toc_lookup",
    "clean_markdown",
    "clean_plain_text",
    "extract_epub_metadata",
    "extract_epub_chapters_dynamic",
    "get_group_leaf_title",
    "join_group_path",
    "load_chapters",
    "load_epub_toc_from_path",
    "sanitize_filename_component",
    "slugify",
    "split_group_path",
    "split_markdown_chapters",
    "strip_front_matter",
]
