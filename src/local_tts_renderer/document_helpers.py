from __future__ import annotations

import html
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .sources.model import SourceChapter, SourceNavigationNode

GROUP_PATH_SEPARATOR = " / "


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip()).strip("-").lower()
    return slug or "document"


def sanitize_filename_component(value: str) -> str:
    value = html.unescape(value).replace("\r\n", "\n")
    value = re.sub(r"\s+", " ", value.replace("\n", " ")).strip()
    value = value.replace(":", " -")
    value = re.sub(r'[<>:"/\\\\|?*]', "-", value)
    value = re.sub(r"\s*-\s*", " - ", value)
    value = re.sub(r"\s+", " ", value).strip(" .-")
    return value or "Document"


def clean_plain_text(text: str) -> str:
    text = html.unescape(text)
    text = text.replace("\r\n", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def join_group_path(parts: list[str]) -> str | None:
    cleaned = [part.strip() for part in parts if part and part.strip()]
    return GROUP_PATH_SEPARATOR.join(cleaned) if cleaned else None


def split_group_path(group: str | None) -> list[str]:
    if not group:
        return []
    return [part.strip() for part in group.split(GROUP_PATH_SEPARATOR) if part.strip()]


def get_group_leaf_title(group: str | None) -> str:
    if not group:
        return "Chapter"
    return split_group_path(group)[-1]


def build_chapter_number_map(chapters: list[SourceChapter]) -> dict[int, int]:
    counters: dict[str | None, int] = {}
    mapping: dict[int, int] = {}
    for index, chapter in enumerate(chapters, start=1):
        key = chapter.group
        counters[key] = counters.get(key, 0) + 1
        mapping[index] = counters[key]
    return mapping


def build_group_directory_map(chapters: list[SourceChapter]) -> dict[str, Path]:
    counters_by_parent: dict[str, dict[str, int]] = {}
    mapping: dict[str, Path] = {}
    for chapter in chapters:
        if not chapter.group or chapter.group in mapping:
            continue
        raw_parts = split_group_path(chapter.group)
        parent_parts: list[str] = []
        numbered_parts: list[str] = []
        for raw_part in raw_parts:
            parent_key = join_group_path(parent_parts) or ""
            sibling_map = counters_by_parent.setdefault(parent_key, {})
            if raw_part not in sibling_map:
                sibling_map[raw_part] = len(sibling_map) + 1
            numbered_parts.append(f"{sibling_map[raw_part]:02d}-{slugify(raw_part)}")
            parent_parts.append(raw_part)
        mapping[chapter.group] = Path(*numbered_parts)
    return mapping


def build_group_directory_map_from_navigation(
    nodes: list[SourceNavigationNode],
    selected_groups: set[str],
) -> dict[str, Path]:
    mapping: dict[str, Path] = {}

    def walk(level_nodes: list[SourceNavigationNode], parent_titles: list[str], parent_dirs: list[str]) -> None:
        for index, node in enumerate(level_nodes, start=1):
            current_titles = [*parent_titles, node.title]
            current_group = join_group_path(current_titles)
            current_dir = f"{index:02d}-{sanitize_filename_component(node.title)}"
            current_dirs = [*parent_dirs, current_dir]
            if current_group in selected_groups:
                mapping[current_group] = Path(*(current_dirs if node.children else parent_dirs))
            if node.children:
                walk(node.children, current_titles, current_dirs)

    walk(nodes, [], [])
    return mapping


__all__ = [
    "GROUP_PATH_SEPARATOR",
    "build_chapter_number_map",
    "build_group_directory_map",
    "build_group_directory_map_from_navigation",
    "clean_plain_text",
    "get_group_leaf_title",
    "join_group_path",
    "sanitize_filename_component",
    "slugify",
    "split_group_path",
]
