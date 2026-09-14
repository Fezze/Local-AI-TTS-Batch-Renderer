from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .atomic_io import write_json_atomic
from .input_paths import source_cache_key
from .scheduler_types import ChapterJob
from .sources import MarkdownIngestOptions, SourceLoadOptions, load_source
from .sources.model import SourceChapter, SourceDocument, SourceMetadata, SourceNavigationNode


SOURCE_SCAN_CACHE_VERSION = 2


def _navigation_to_payload(nodes: list[SourceNavigationNode]) -> list[dict[str, object]]:
    return [
        {"title": node.title, "href": node.href, "children": _navigation_to_payload(node.children)}
        for node in nodes
    ]


def _navigation_from_payload(value: object) -> list[SourceNavigationNode]:
    if not isinstance(value, list):
        raise ValueError
    nodes: list[SourceNavigationNode] = []
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("title"), str):
            raise ValueError
        href = item.get("href")
        if href is not None and not isinstance(href, str):
            raise ValueError
        nodes.append(SourceNavigationNode(title=item["title"], href=href, children=_navigation_from_payload(item.get("children", []))))
    return nodes


def _document_from_payload(source_path: Path, value: object) -> SourceDocument:
    if not isinstance(value, dict):
        raise ValueError
    metadata = value.get("metadata")
    chapters = value.get("chapters")
    if not isinstance(metadata, dict) or not isinstance(metadata.get("source_title"), str) or not isinstance(chapters, list):
        raise ValueError
    parsed_chapters: list[SourceChapter] = []
    for chapter in chapters:
        if not isinstance(chapter, dict) or not isinstance(chapter.get("title"), str) or not isinstance(chapter.get("text"), str):
            raise ValueError
        group = chapter.get("group")
        if group is not None and not isinstance(group, str):
            raise ValueError
        parsed_chapters.append(SourceChapter(title=chapter["title"], text=chapter["text"], group=group))
    return SourceDocument(
        path=source_path,
        metadata=SourceMetadata(
            source_title=metadata["source_title"],
            author=metadata.get("author") if isinstance(metadata.get("author"), str) else None,
            publisher=metadata.get("publisher") if isinstance(metadata.get("publisher"), str) else None,
            published_date=metadata.get("published_date") if isinstance(metadata.get("published_date"), str) else None,
            language=metadata.get("language") if isinstance(metadata.get("language"), str) else None,
        ),
        chapters=parsed_chapters,
        navigation=_navigation_from_payload(value.get("navigation", [])),
    )


def load_document_for_jobs(
    source_path: Path,
    cache_root: Path,
    md_single_chapter: bool,
    max_chapter_chars: int,
    md_chapter_heading_level: int,
) -> tuple[SourceDocument, bool]:
    stat = source_path.stat()
    with source_path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    source_state = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": digest}
    markdown_options = {
        "single_chapter": md_single_chapter,
        "max_chapter_chars": max_chapter_chars,
        "chapter_heading_level": md_chapter_heading_level,
    }
    cache_path = cache_root / f"{source_cache_key(source_path)}.json"
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if (
            cached.get("version") == SOURCE_SCAN_CACHE_VERSION
            and cached.get("source") == source_state
            and cached.get("markdown") == markdown_options
        ):
            return _document_from_payload(source_path, cached.get("document")), True
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, AttributeError):
        pass

    document = load_source(
        source_path,
        SourceLoadOptions(
            markdown=MarkdownIngestOptions(
                single_chapter=md_single_chapter,
                max_chapter_chars=max_chapter_chars,
                chapter_heading_level=md_chapter_heading_level,
            )
        ),
    )
    write_json_atomic(
        cache_path,
        {
            "version": SOURCE_SCAN_CACHE_VERSION,
            "source": source_state,
            "markdown": markdown_options,
            "document": {
                "metadata": document.metadata.__dict__,
                "chapters": [chapter.__dict__ for chapter in document.chapters],
                "navigation": _navigation_to_payload(document.navigation),
            },
        },
    )
    return document, False


def build_jobs_for_source(
    source_path: Path,
    output_dir: Path,
    fresh: bool,
    debug: bool,
    force: bool,
    md_single_chapter: bool,
    max_chapter_chars: int,
    md_chapter_heading_level: int,
    job_max_chars: int,
    max_chars: int,
    max_phoneme_chars: int,
) -> tuple[list[ChapterJob], list[ChapterJob], dict[Path, Path]]:
    from .scheduler_jobs import build_jobs

    return build_jobs(
        [source_path],
        output_dir,
        fresh,
        debug,
        force=force,
        md_single_chapter=md_single_chapter,
        max_chapter_chars=max_chapter_chars,
        md_chapter_heading_level=md_chapter_heading_level,
        job_max_chars=job_max_chars,
        max_chars=max_chars,
        max_phoneme_chars=max_phoneme_chars,
    )


__all__ = ["build_jobs_for_source", "load_document_for_jobs"]
