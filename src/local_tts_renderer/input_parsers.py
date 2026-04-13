from __future__ import annotations

import html
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

GROUP_PATH_SEPARATOR = " / "


@dataclass
class Chapter:
    title: str
    text: str
    group: str | None = None


@dataclass
class TocNode:
    title: str
    href: str | None = None
    children: list["TocNode"] | None = None


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


def clean_plain_text(text: str) -> str:
    text = html.unescape(text)
    text = text.replace("\r\n", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


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


def split_markdown_chapters(text: str, fallback_title: str, *, single_chapter: bool = False, max_chapter_chars: int = 0) -> list[Chapter]:
    if single_chapter:
        cleaned = clean_markdown(text)
        if max_chapter_chars > 0 and len(cleaned) > max_chapter_chars:
            return [
                Chapter(title=fallback_title if index == 1 else f"{fallback_title} {index}", text=part)
                for index, part in enumerate(_split_text_by_limit(cleaned, max_chapter_chars), start=1)
            ]
        return [Chapter(title=fallback_title, text=cleaned)]
    lines = text.replace("\r\n", "\n").splitlines()
    chapters: list[Chapter] = []
    current_title: str | None = None
    current_lines: list[str] = []
    for line in lines:
        match = re.match(r"^#\s+(.+)$", line.strip())
        if match:
            if current_lines:
                chapter_text = clean_markdown("\n".join(current_lines))
                if chapter_text:
                    chapters.append(Chapter(title=current_title or fallback_title, text=chapter_text))
            current_title = match.group(1).strip()
            current_lines = []
            continue
        current_lines.append(line)
    if current_lines:
        chapter_text = clean_markdown("\n".join(current_lines))
        if chapter_text:
            chapters.append(Chapter(title=current_title or fallback_title, text=chapter_text))
    if max_chapter_chars > 0:
        split_chapters: list[Chapter] = []
        for chapter in chapters or [Chapter(title=fallback_title, text=clean_markdown(text))]:
            pieces = _split_text_by_limit(chapter.text, max_chapter_chars)
            if len(pieces) == 1:
                split_chapters.append(chapter)
                continue
            for index, piece in enumerate(pieces, start=1):
                split_chapters.append(Chapter(title=f"{chapter.title} {index}", text=piece))
        return split_chapters
    return chapters or [Chapter(title=fallback_title, text=clean_markdown(text))]


def normalize_epub_path(base_path: str, href: str) -> str:
    base_dir = str(PurePosixPath(base_path).parent)
    if base_dir == ".":
        return posixpath.normpath(href)
    return posixpath.normpath(posixpath.join(base_dir, href))


def strip_href_fragment(href: str) -> str:
    return href.split("#", 1)[0]


def parse_ncx_navpoints(navpoints: list[ET.Element], package_path: str) -> list[TocNode]:
    nodes: list[TocNode] = []
    for navpoint in navpoints:
        text_node = navpoint.find(".//{*}navLabel/{*}text")
        content_node = navpoint.find("{*}content")
        title = clean_plain_text("".join(text_node.itertext())) if text_node is not None else "Untitled"
        href = None
        if content_node is not None and "src" in content_node.attrib:
            href = normalize_epub_path(package_path, strip_href_fragment(content_node.attrib["src"]))
        children = parse_ncx_navpoints(navpoint.findall("{*}navPoint"), package_path)
        nodes.append(TocNode(title=title, href=href, children=children))
    return nodes


def load_epub_toc(archive: zipfile.ZipFile, package_path: str, package_xml: ET.Element) -> list[TocNode]:
    manifest_items = package_xml.findall(".//{*}manifest/{*}item")
    ncx_href = None
    for item in manifest_items:
        media_type = item.attrib.get("media-type", "")
        if media_type == "application/x-dtbncx+xml":
            ncx_href = normalize_epub_path(package_path, item.attrib["href"])
            break
    if not ncx_href:
        return []
    ncx_xml = ET.fromstring(archive.read(ncx_href))
    nav_map = ncx_xml.find(".//{*}navMap")
    if nav_map is None:
        return []
    return parse_ncx_navpoints(nav_map.findall("{*}navPoint"), package_path)


def load_epub_toc_from_path(path: Path) -> list[TocNode]:
    with zipfile.ZipFile(path) as archive:
        container_xml = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = container_xml.find(".//{*}rootfile")
        if rootfile is None:
            raise RuntimeError(f"EPUB container missing rootfile: {path}")
        package_path = rootfile.attrib["full-path"]
        package_xml = ET.fromstring(archive.read(package_path))
        return load_epub_toc(archive, package_path, package_xml)


def build_toc_lookup(nodes: list[TocNode], path: list[str] | None = None, lookup: dict[str, tuple[list[str], bool]] | None = None) -> dict[str, tuple[list[str], bool]]:
    path = path or []
    lookup = lookup or {}
    for node in nodes:
        current_path = [*path, node.title]
        has_children = bool(node.children)
        if node.href:
            lookup[node.href] = (current_path, has_children)
        if node.children:
            build_toc_lookup(node.children, current_path, lookup)
    return lookup


def join_group_path(parts: list[str]) -> str | None:
    cleaned = [part.strip() for part in parts if part and part.strip()]
    return GROUP_PATH_SEPARATOR.join(cleaned) if cleaned else None


def split_group_path(group: str | None) -> list[str]:
    if not group:
        return []
    return [part.strip() for part in group.split(GROUP_PATH_SEPARATOR) if part.strip()]


def build_group_directory_map(chapters: list[Chapter]) -> dict[str, Path]:
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


def build_group_directory_map_from_toc(nodes: list[TocNode], selected_groups: set[str]) -> dict[str, Path]:
    mapping: dict[str, Path] = {}

    def walk(level_nodes: list[TocNode], parent_titles: list[str], parent_dirs: list[str]) -> None:
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


def extract_epub_chapters_dynamic(path: Path) -> list[Chapter]:
    chapters: list[Chapter] = []
    with zipfile.ZipFile(path) as archive:
        container_xml = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = container_xml.find(".//{*}rootfile")
        if rootfile is None:
            raise RuntimeError(f"EPUB container missing rootfile: {path}")
        package_path = rootfile.attrib["full-path"]
        package_xml = ET.fromstring(archive.read(package_path))
        manifest = {
            item.attrib["id"]: normalize_epub_path(package_path, item.attrib["href"])
            for item in package_xml.findall(".//{*}manifest/{*}item")
            if "id" in item.attrib and "href" in item.attrib
        }
        spine_ids = [item.attrib["idref"] for item in package_xml.findall(".//{*}spine/{*}itemref") if "idref" in item.attrib]
        toc_lookup = build_toc_lookup(load_epub_toc(archive, package_path, package_xml))
        for spine_id in spine_ids:
            item_path = manifest.get(spine_id)
            if not item_path or not item_path.lower().endswith((".xhtml", ".html", ".htm", ".xml")):
                continue
            try:
                doc = ET.fromstring(archive.read(item_path))
            except ET.ParseError:
                continue
            body = doc.find(".//{*}body")
            if body is None:
                continue
            title_node = body.find(".//{*}h1") or body.find(".//{*}h2") or doc.find(".//{*}title")
            title = clean_plain_text(" ".join(title_node.itertext())) if title_node is not None else PurePosixPath(item_path).stem
            text = clean_plain_text("\n".join(body.itertext()))
            if not text:
                continue
            normalized_title = title or PurePosixPath(item_path).stem
            toc_entry = toc_lookup.get(item_path)
            group = None
            if toc_entry:
                toc_path, has_children = toc_entry
                group_parts = toc_path if has_children else toc_path[:-1]
                group = join_group_path(group_parts)
                if toc_path:
                    normalized_title = toc_path[-1]
            chapters.append(Chapter(title=normalized_title, text=text, group=group))
    if not chapters:
        raise RuntimeError(f"No readable spine chapters found in EPUB: {path}")
    return chapters


def load_chapters(source_path: Path, *, single_chapter: bool = False, max_chapter_chars: int = 0) -> list[Chapter]:
    if source_path.suffix.lower() == ".epub":
        return extract_epub_chapters_dynamic(source_path)
    raw_text = source_path.read_text(encoding="utf-8")
    return split_markdown_chapters(raw_text, fallback_title=source_path.stem, single_chapter=single_chapter, max_chapter_chars=max_chapter_chars)


def build_chapter_number_map(chapters: list[Chapter]) -> dict[int, int]:
    counters: dict[str | None, int] = {}
    mapping: dict[int, int] = {}
    for index, chapter in enumerate(chapters, start=1):
        key = chapter.group
        counters[key] = counters.get(key, 0) + 1
        mapping[index] = counters[key]
    return mapping


__all__ = [
    "Chapter",
    "TocNode",
    "build_chapter_number_map",
    "build_group_directory_map",
    "build_group_directory_map_from_toc",
    "load_chapters",
    "load_epub_toc_from_path",
    "sanitize_filename_component",
    "slugify",
    "split_group_path",
]
