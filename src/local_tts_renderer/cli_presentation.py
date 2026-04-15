from __future__ import annotations

from pathlib import Path

from .input_parsers import Chapter, TocNode, sanitize_filename_component, slugify, split_group_path


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
