from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceLoadOptions:
    markdown_single_chapter: bool = False
    markdown_max_chapter_chars: int = 0


__all__ = ["SourceLoadOptions"]
