from __future__ import annotations

import posixpath
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from ..document_helpers import clean_plain_text


@dataclass
class TocNode:
    title: str
    href: str | None = None
    children: list['TocNode'] | None = None


def normalize_epub_path(base_path: str, href: str) -> str:
    url = urlsplit(href)
    if url.scheme or url.netloc:
        return href
    path = posixpath.normpath(posixpath.join(posixpath.dirname(base_path), unquote(url.path))) if url.path else base_path
    return path + ('#' + unquote(url.fragment) if url.fragment else '')


def strip_href_fragment(href: str) -> str:
    return href.split('#', 1)[0]


def parse_ncx_navpoints(navpoints: list[ET.Element], package_path: str) -> list[TocNode]:
    nodes = []
    for point in navpoints:
        label = point.find('{*}navLabel/{*}text')
        target = point.find('{*}content')
        nodes.append(TocNode(
            title=clean_plain_text(''.join(label.itertext())) if label is not None else 'Untitled',
            href=normalize_epub_path(package_path, target.attrib['src']) if target is not None and 'src' in target.attrib else None,
            children=parse_ncx_navpoints(point.findall('{*}navPoint'), package_path),
        ))
    return nodes


def _parse_nav_list(ordered: ET.Element | None, path: str) -> list[TocNode]:
    if ordered is None:
        return []
    nodes = []
    for item in ordered.findall('{*}li'):
        label = item.find('{*}a')
        if label is None:
            label = item.find('{*}span')
        if label is None:
            continue
        href = label.get('href')
        nodes.append(TocNode(
            clean_plain_text(''.join(label.itertext())),
            normalize_epub_path(path, href) if href else None,
            _parse_nav_list(item.find('{*}ol'), path),
        ))
    return nodes


def load_epub_toc(archive: zipfile.ZipFile, package_path: str, package_xml: ET.Element) -> list[TocNode]:
    items = package_xml.findall('.//{*}manifest/{*}item')
    for item in items:
        if 'nav' not in item.get('properties', '').split() or not item.get('href'):
            continue
        path = normalize_epub_path(package_path, item.attrib['href'])
        doc = ET.fromstring(archive.read(path))
        for nav in doc.findall('.//{*}nav'):
            types = nav.get('{http://www.idpf.org/2007/ops}type', '').split()
            if 'toc' in types or nav.get('role') == 'doc-toc':
                return _parse_nav_list(nav.find('{*}ol'), path)
    for item in items:
        if item.get('media-type') != 'application/x-dtbncx+xml' or not item.get('href'):
            continue
        path = normalize_epub_path(package_path, item.attrib['href'])
        doc = ET.fromstring(archive.read(path))
        nav = doc.find('.//{*}navMap')
        if nav is not None:
            return parse_ncx_navpoints(nav.findall('{*}navPoint'), path)
    return []


def build_toc_lookup(nodes: list[TocNode], path: list[str] | None = None,
                     lookup: dict[str, tuple[list[str], bool]] | None = None) -> dict[str, tuple[list[str], bool]]:
    path = path or []
    if lookup is None:
        lookup = {}
    for node in nodes:
        current = [*path, node.title]
        if node.href:
            lookup[node.href] = (current, bool(node.children))
        if node.children:
            build_toc_lookup(node.children, current, lookup)
    return lookup
