from __future__ import annotations

import json
from pathlib import Path

from .input_parsers import Chapter


def load_chapters_from_cache(cache_path: Path) -> list[Chapter]:
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    chapters: list[Chapter] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        chapters.append(
            Chapter(
                title=str(item.get("title", "Untitled")),
                text=str(item.get("text", "")),
                group=item.get("group"),
            )
        )
    return chapters


__all__ = ["load_chapters_from_cache"]
