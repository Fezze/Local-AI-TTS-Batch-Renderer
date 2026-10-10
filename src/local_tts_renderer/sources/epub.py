from __future__ import annotations

import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from ..document_helpers import clean_plain_text, join_group_path
from .model import SourceChapter, SourceDocument, SourceMetadata, SourceNavigationNode
from .registry_types import SourceLoadOptions
from .epub_navigation import (TocNode, normalize_epub_path, strip_href_fragment,
                              parse_ncx_navpoints, load_epub_toc, build_toc_lookup)
from .epub_structure import ChapterCollector


SUPPORTED_SUFFIXES = frozenset({".epub"})


def can_load(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_SUFFIXES


def load_epub_toc_from_path(path: Path) -> list[TocNode]:
    with zipfile.ZipFile(path) as archive:
        container_xml = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = container_xml.find(".//{*}rootfile")
        if rootfile is None:
            raise RuntimeError(f"EPUB container missing rootfile: {path}")
        package_path = rootfile.attrib["full-path"]
        package_xml = ET.fromstring(archive.read(package_path))
        return load_epub_toc(archive, package_path, package_xml)


def extract_epub_chapters_dynamic(path: Path) -> list[SourceChapter]:
    collector = ChapterCollector(extract_epub_metadata(path).source_title)
    with zipfile.ZipFile(path) as archive:
        container_xml = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = container_xml.find(".//{*}rootfile")
        if rootfile is None:
            raise RuntimeError(f"EPUB container missing rootfile: {path}")
        package_path = rootfile.attrib["full-path"]
        package_xml = ET.fromstring(archive.read(package_path))
        manifest = {
            item.attrib["id"]: (
                normalize_epub_path(package_path, item.attrib["href"]),
                item.attrib.get("media-type", "").lower(),
            )
            for item in package_xml.findall(".//{*}manifest/{*}item")
            if "id" in item.attrib and "href" in item.attrib
        }
        spine_ids = [item.attrib["idref"] for item in package_xml.findall(".//{*}spine/{*}itemref") if "idref" in item.attrib]
        toc_lookup = build_toc_lookup(load_epub_toc(archive, package_path, package_xml))
        for spine_id in spine_ids:
            manifest_item = manifest.get(spine_id)
            if not manifest_item:
                continue
            item_path, media_type = manifest_item
            is_document = media_type in {"application/xhtml+xml", "text/html", "application/xml"}
            if not is_document and not item_path.lower().endswith((".xhtml", ".html", ".htm", ".xml")):
                continue
            try:
                doc = ET.fromstring(archive.read(item_path))
            except ET.ParseError:
                continue
            body = doc.find(".//{*}body")
            if body is None:
                continue
            collector.add_document(body, item_path, toc_lookup)
    chapters = collector.chapters
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
