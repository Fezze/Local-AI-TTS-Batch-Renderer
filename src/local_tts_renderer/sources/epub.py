from __future__ import annotations

import posixpath
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath

from ..document_helpers import clean_plain_text, join_group_path
from .model import SourceChapter, SourceDocument, SourceMetadata, SourceNavigationNode
from .registry_types import SourceLoadOptions


SUPPORTED_SUFFIXES = frozenset({".epub"})


@dataclass
class TocNode:
    title: str
    href: str | None = None
    children: list["TocNode"] | None = None


def can_load(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_SUFFIXES


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


def extract_epub_chapters_dynamic(path: Path) -> list[SourceChapter]:
    chapters: list[SourceChapter] = []
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
            title_node = body.find(".//{*}h1")
            if title_node is None:
                title_node = body.find(".//{*}h2")
            if title_node is None:
                title_node = doc.find(".//{*}title")
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
            chapters.append(SourceChapter(title=normalized_title, text=text, group=group))
    if not chapters:
        raise RuntimeError(f"No readable spine chapters found in EPUB: {path}")
    return chapters


def extract_epub_metadata(path: Path) -> SourceMetadata:
    metadata = SourceMetadata(source_title=path.stem)
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

        return SourceMetadata(
            source_title=clean_plain_text(title_node.text) if title_node is not None and title_node.text else metadata.source_title,
            author=clean_plain_text(creator_node.text) if creator_node is not None and creator_node.text else None,
            publisher=clean_plain_text(publisher_node.text) if publisher_node is not None and publisher_node.text else None,
            published_date=clean_plain_text(date_node.text) if date_node is not None and date_node.text else None,
            language=clean_plain_text(language_node.text) if language_node is not None and language_node.text else None,
        )
    return metadata


def _navigation_from_toc(nodes: list[TocNode]) -> list[SourceNavigationNode]:
    return [
        SourceNavigationNode(
            title=node.title,
            href=node.href,
            children=_navigation_from_toc(node.children or []),
        )
        for node in nodes
    ]


def load(path: Path, options: SourceLoadOptions | None = None) -> SourceDocument:
    metadata = extract_epub_metadata(path)
    chapters = extract_epub_chapters_dynamic(path)
    toc_nodes = load_epub_toc_from_path(path)
    return SourceDocument(
        path=path,
        metadata=SourceMetadata(
            source_title=metadata.source_title,
            author=metadata.author,
            publisher=metadata.publisher,
            published_date=metadata.published_date,
            language=metadata.language,
        ),
        chapters=[SourceChapter(title=chapter.title, text=chapter.text, group=chapter.group) for chapter in chapters],
        navigation=_navigation_from_toc(toc_nodes),
    )


__all__ = [
    "SUPPORTED_SUFFIXES",
    "TocNode",
    "can_load",
    "extract_epub_chapters_dynamic",
    "extract_epub_metadata",
    "load",
    "load_epub_toc_from_path",
]
