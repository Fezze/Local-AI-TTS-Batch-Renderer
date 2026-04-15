from __future__ import annotations

import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from .cli_models import AudioMetadata
from .cli_presentation import print_chapter_summary, print_output_structure_preview, print_toc_tree
from .input_parsers import (
    Chapter,
    TocNode,
    build_chapter_number_map,
    build_group_directory_map,
    build_group_directory_map_from_toc,
    clean_markdown,
    clean_plain_text,
    join_group_path,
    load_chapters,
    load_epub_toc_from_path,
    sanitize_filename_component,
    slugify,
    split_group_path,
    split_markdown_chapters,
)


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


def get_group_leaf_title(group: str | None) -> str:
    if not group:
        return "Chapter"
    return split_group_path(group)[-1]


def strip_front_matter(text: str) -> str:
    import re

    return re.sub(r"\A---\s*\n.*?\n---\s*\n", "", text, flags=re.DOTALL)


def summarize_chapters(chapters: list[Chapter]) -> list[dict]:
    summary: list[dict] = []
    for index, chapter in enumerate(chapters, start=1):
        words = chapter.text.split()
        summary.append(
            {
                "index": index,
                "title": chapter.title,
                "group": chapter.group,
                "chars": len(chapter.text),
                "words": len(words),
                "preview": " ".join(words[:20]),
            }
        )
    return summary


def extract_epub_metadata(path: Path) -> AudioMetadata:
    metadata = AudioMetadata(source_title=path.stem)
    with zipfile.ZipFile(path) as archive:
        container_xml = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = container_xml.find(".//{*}rootfile")
        if rootfile is None:
            return metadata
        package_path = rootfile.attrib["full-path"]
        package_xml = ET.fromstring(archive.read(package_path))
        metadata_node = package_xml.find(".//{*}metadata")
        if metadata_node is None:
            return metadata

        title_node = metadata_node.find("{*}title")
        creator_node = metadata_node.find("{*}creator")
        publisher_node = metadata_node.find("{*}publisher")
        date_node = metadata_node.find("{*}date")
        language_node = metadata_node.find("{*}language")

        if title_node is not None and title_node.text:
            metadata.source_title = clean_plain_text(title_node.text)
        if creator_node is not None and creator_node.text:
            metadata.author = clean_plain_text(creator_node.text)
        if publisher_node is not None and publisher_node.text:
            metadata.publisher = clean_plain_text(publisher_node.text)
        if date_node is not None and date_node.text:
            metadata.published_date = clean_plain_text(date_node.text)
        if language_node is not None and language_node.text:
            metadata.language = clean_plain_text(language_node.text)
    return metadata


__all__ = [
    "Chapter",
    "TocNode",
    "build_chapter_number_map",
    "build_group_directory_map",
    "build_group_directory_map_from_toc",
    "clean_markdown",
    "extract_epub_metadata",
    "get_group_leaf_title",
    "join_group_path",
    "load_chapters",
    "load_chapters_from_cache",
    "load_epub_toc_from_path",
    "print_chapter_summary",
    "print_output_structure_preview",
    "print_toc_tree",
    "sanitize_filename_component",
    "slugify",
    "split_group_path",
    "split_markdown_chapters",
    "strip_front_matter",
    "summarize_chapters",
]
