from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SourceMetadata:
    source_title: str
    author: str | None = None
    publisher: str | None = None
    published_date: str | None = None
    language: str | None = None


@dataclass(frozen=True)
class SourceChapter:
    title: str
    text: str
    group: str | None = None


@dataclass(frozen=True)
class SourceNavigationNode:
    title: str
    href: str | None = None
    children: list["SourceNavigationNode"] = field(default_factory=list)


@dataclass(frozen=True)
class SourceDocument:
    path: Path
    metadata: SourceMetadata
    chapters: list[SourceChapter]
    navigation: list[SourceNavigationNode] = field(default_factory=list)


__all__ = [
    "SourceChapter",
    "SourceDocument",
    "SourceMetadata",
    "SourceNavigationNode",
]
