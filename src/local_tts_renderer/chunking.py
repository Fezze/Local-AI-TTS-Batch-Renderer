from __future__ import annotations

from typing import Any


def _cli() -> Any:
    from . import cli

    return cli


def build_chunks(*args, **kwargs):
    return _cli().build_chunks(*args, **kwargs)


def chunk_section(*args, **kwargs):
    return _cli().chunk_section(*args, **kwargs)


def split_paragraphs(*args, **kwargs):
    return _cli().split_paragraphs(*args, **kwargs)


def split_sentences(*args, **kwargs):
    return _cli().split_sentences(*args, **kwargs)


def split_text_for_retry(*args, **kwargs):
    return _cli().split_text_for_retry(*args, **kwargs)


__all__ = [
    "build_chunks",
    "chunk_section",
    "split_paragraphs",
    "split_sentences",
    "split_text_for_retry",
]
