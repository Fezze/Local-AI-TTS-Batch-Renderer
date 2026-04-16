from __future__ import annotations

from pathlib import Path

from .model import SourceChapter, SourceDocument, SourceMetadata, SourceNavigationNode
from .registry_types import SourceLoadOptions
from ..input_parsers import TocNode, extract_epub_chapters_dynamic, extract_epub_metadata, load_epub_toc_from_path


SUPPORTED_SUFFIXES = frozenset({".epub"})


def can_load(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_SUFFIXES


def _navigation_from_toc(nodes: list[TocNode]) -> list[SourceNavigationNode]:
    return [
        SourceNavigationNode(
            title=node.title,
            href=node.href,
            children=_navigation_from_toc(node.children or []),
        )
        for node in nodes
    ]


def load(path: Path, options: SourceLoadOptions | None = None) -> SourceDocument:
    metadata = extract_epub_metadata(path)
    chapters = extract_epub_chapters_dynamic(path)
    toc_nodes = load_epub_toc_from_path(path)
    return SourceDocument(
        path=path,
        metadata=SourceMetadata(
            source_title=metadata.source_title,
            author=metadata.author,
            publisher=metadata.publisher,
            published_date=metadata.published_date,
            language=metadata.language,
        ),
        chapters=[SourceChapter(title=chapter.title, text=chapter.text, group=chapter.group) for chapter in chapters],
        navigation=_navigation_from_toc(toc_nodes),
    )


__all__ = ["SUPPORTED_SUFFIXES", "can_load", "load"]
