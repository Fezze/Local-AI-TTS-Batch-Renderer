from __future__ import annotations

from pathlib import Path
from types import ModuleType

from . import epub, markdown
from .model import SourceDocument
from .registry_types import MarkdownIngestOptions, SourceLoadOptions

INGESTERS: tuple[ModuleType, ...] = (markdown, epub)


def supported_suffixes() -> set[str]:
    suffixes: set[str] = set()
    for ingester in INGESTERS:
        suffixes.update(getattr(ingester, "SUPPORTED_SUFFIXES"))
    return suffixes


def can_load(path: Path) -> bool:
    return any(ingester.can_load(path) for ingester in INGESTERS)


def load_source(path: Path, options: SourceLoadOptions | None = None) -> SourceDocument:
    for ingester in INGESTERS:
        if ingester.can_load(path):
            return ingester.load(path, options)
    raise ValueError(f"Unsupported source format: {path}")


__all__ = ["MarkdownIngestOptions", "SourceLoadOptions", "can_load", "load_source", "supported_suffixes"]
