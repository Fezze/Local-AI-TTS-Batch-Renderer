from __future__ import annotations

import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

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


def strip_front_matter(text: str) -> str:
    import re

    return re.sub(r"\A---\s*\n.*?\n---\s*\n", "", text, flags=re.DOTALL)


def extract_epub_metadata(path: Path):
    from .cli_models import AudioMetadata

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


def print_chapter_summary(source_path: Path, chapters: list[Chapter]) -> None:
    print(f"Source: {source_path}")
    for item in summarize_chapters(chapters):
        group = f" [{item['group']}]" if item["group"] else ""
        print(
            f"{item['index']:3d}. {item['title']}{group}\n"
            f"     chars={item['chars']} words={item['words']} preview={item['preview']}"
        )


def print_toc_tree(nodes: list[TocNode], depth: int = 0) -> None:
    for node in nodes:
        indent = "  " * depth
        href = f" -> {node.href}" if node.href else ""
        print(f"{indent}- {node.title}{href}")
        if node.children:
            print_toc_tree(node.children, depth + 1)


def print_output_structure_preview(source_path: Path, chapters: list[Chapter]) -> None:
    output_root_name = slugify(source_path.stem)
    source_groups = [chapter.group for chapter in chapters if chapter.group]
    if source_groups:
        print(f"out/{output_root_name}/")
        group_paths = sorted({chapter.group for chapter in chapters if chapter.group})
        grouped_titles: dict[str | None, list[str]] = {}
        for chapter in chapters:
            grouped_titles.setdefault(chapter.group, []).append(sanitize_filename_component(chapter.title))

        for group in group_paths:
            path_parts = split_group_path(group)
            for depth in range(len(path_parts)):
                indent = "  " * (depth + 1)
                print(f"{indent}{path_parts[depth]}/")
            file_indent = "  " * (len(path_parts) + 1)
            for title_slug in grouped_titles.get(group, []):
                print(f"{file_indent}{title_slug}")
    else:
        print(f"out/{output_root_name}/")

