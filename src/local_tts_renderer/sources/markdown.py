from __future__ import annotations

import re
from pathlib import Path

from .model import SourceChapter, SourceDocument, SourceMetadata
from .registry_types import SourceLoadOptions


SUPPORTED_SUFFIXES = frozenset({".md", ".markdown"})


def can_load(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_SUFFIXES


def clean_markdown(text: str) -> str:
    text = text.replace("\r\n", "\n")
    text = re.sub(r"\A---\s*\n.*?\n---\s*\n", "", text, flags=re.DOTALL)
    text = re.sub(r"```.*?```", "\n", text, flags=re.DOTALL)
    text = re.sub(r"~~~.*?~~~", "\n", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"<[^>]+>", " ", text)
    cleaned_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            cleaned_lines.append("")
            continue
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"^>\s*", "", line)
        line = re.sub(r"^\s*[-*+]\s+", "", line)
        line = re.sub(r"^\s*\d+\.\s+", "", line)
        if re.fullmatch(r"[-|:\s]+", line):
            continue
        line = line.replace("|", " ")
        line = line.replace("*", "")
        line = line.replace("_", " ")
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def strip_front_matter(text: str) -> str:
    return re.sub(r"\A---\s*\n.*?\n---\s*\n", "", text, flags=re.DOTALL)


def _split_text_by_limit(text: str, max_length: int) -> list[str]:
    if max_length <= 0 or len(text) <= max_length:
        return [text]
    parts: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_length)
        if end < len(text):
            split_at = text.rfind("\n\n", start, end)
            if split_at == -1 or split_at <= start:
                split_at = text.rfind(" ", start, end)
            if split_at == -1 or split_at <= start:
                split_at = end
            else:
                split_at += 2 if text[split_at:split_at + 2] == "\n\n" else 1
            end = split_at
        chunk = text[start:end].strip()
        if chunk:
            parts.append(chunk)
        start = end
        while start < len(text) and text[start].isspace():
            start += 1
    return parts or [text]


def split_markdown_chapters(
    text: str,
    fallback_title: str,
    *,
    single_chapter: bool = False,
    max_chapter_chars: int = 0,
) -> list[SourceChapter]:
    if single_chapter:
        cleaned = clean_markdown(text)
        if max_chapter_chars > 0 and len(cleaned) > max_chapter_chars:
            return [
                SourceChapter(title=fallback_title if index == 1 else f"{fallback_title} {index}", text=part)
                for index, part in enumerate(_split_text_by_limit(cleaned, max_chapter_chars), start=1)
            ]
        return [SourceChapter(title=fallback_title, text=cleaned)]
    lines = text.replace("\r\n", "\n").splitlines()
    chapters: list[SourceChapter] = []
    current_title: str | None = None
    current_lines: list[str] = []
    for line in lines:
        match = re.match(r"^#\s+(.+)$", line.strip())
        if match:
            if current_lines:
                chapter_text = clean_markdown("\n".join(current_lines))
                if chapter_text:
                    chapters.append(SourceChapter(title=current_title or fallback_title, text=chapter_text))
            current_title = match.group(1).strip()
            current_lines = []
            continue
        current_lines.append(line)
    if current_lines:
        chapter_text = clean_markdown("\n".join(current_lines))
        if chapter_text:
            chapters.append(SourceChapter(title=current_title or fallback_title, text=chapter_text))
    if max_chapter_chars > 0:
        split_chapters: list[SourceChapter] = []
        for chapter in chapters or [SourceChapter(title=fallback_title, text=clean_markdown(text))]:
            pieces = _split_text_by_limit(chapter.text, max_chapter_chars)
            if len(pieces) == 1:
                split_chapters.append(chapter)
                continue
            for index, piece in enumerate(pieces, start=1):
                split_chapters.append(SourceChapter(title=f"{chapter.title} {index}", text=piece))
        return split_chapters
    return chapters or [SourceChapter(title=fallback_title, text=clean_markdown(text))]


def load(path: Path, options: SourceLoadOptions | None = None) -> SourceDocument:
    options = options or SourceLoadOptions()
    raw_text = path.read_text(encoding="utf-8")
    chapters = split_markdown_chapters(
        raw_text,
        fallback_title=path.stem,
        single_chapter=options.markdown.single_chapter,
        max_chapter_chars=options.markdown.max_chapter_chars,
    )
    return SourceDocument(
        path=path,
        metadata=SourceMetadata(source_title=path.stem),
        chapters=chapters,
    )


__all__ = ["SUPPORTED_SUFFIXES", "can_load", "clean_markdown", "load", "split_markdown_chapters", "strip_front_matter"]
