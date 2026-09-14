from __future__ import annotations

import warnings
import xml.etree.ElementTree as ET
from dataclasses import replace

from ..document_helpers import clean_plain_text, join_group_path
from .model import SourceChapter


class ChapterCollector:
    """Collect logical sections in spine order, across resource boundaries."""

    def __init__(self, fallback_title: str):
        self.fallback_title = fallback_title
        self.chapters: list[SourceChapter] = []

    def add_document(self, body: ET.Element, path: str, toc: dict[str, tuple[list[str], bool]]) -> None:
        text, anchors, headings = _text_positions(body)
        if not text.strip():
            return
        boundaries: dict[int, tuple[str, str | None]] = {}
        fragment_boundaries = False
        for href, (titles, has_children) in toc.items():
            resource, _, fragment = href.partition('#')
            if resource != path:
                continue
            if fragment and fragment not in anchors:
                warnings.warn(f'EPUB TOC target not found: {href}', RuntimeWarning)
                continue
            position = anchors[fragment] if fragment else 0
            boundaries[position] = (titles[-1], join_group_path(titles if has_children else titles[:-1]))
            fragment_boundaries |= bool(fragment)
        # Exact TOC fragments define the author's navigation. Otherwise headings
        # expose chapters inside one HTML resource, regardless of its file size.
        if not fragment_boundaries:
            for position, title in headings:
                if boundaries and not text[:position].strip():
                    continue
                boundaries.setdefault(position, (title, None))
        positions = sorted(boundaries)
        cursor = 0
        current: tuple[str, str | None] | None = None
        for position in [*positions, len(text)]:
            payload = clean_plain_text(text[cursor:position])
            if payload:
                if current is not None:
                    self.chapters.append(SourceChapter(current[0], payload, current[1]))
                    current = None
                elif self.chapters:
                    previous = self.chapters[-1]
                    self.chapters[-1] = replace(previous, text=previous.text + '\n\n' + payload)
                else:
                    self.chapters.append(SourceChapter(self.fallback_title, payload))
            if position in boundaries:
                current = boundaries[position]
            cursor = position


def _text_positions(body: ET.Element) -> tuple[str, dict[str, int], list[tuple[int, str]]]:
    pieces: list[str] = []
    anchors: dict[str, int] = {}
    headings: list[tuple[int, str]] = []
    position = 0

    def append(value: str | None) -> None:
        nonlocal position
        if value is not None:
            pieces.append(value)
            position += len(value)

    def walk(element: ET.Element) -> None:
        tag = element.tag.rsplit('}', 1)[-1]
        if tag in {'script', 'style'}:
            return
        block = tag in {'p', 'div', 'section', 'article', 'li', 'blockquote', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
        if block:
            append('\n\n')
        if tag == 'br':
            append('\n')
        identifier = element.get('id') or element.get('{http://www.w3.org/XML/1998/namespace}id')
        if identifier:
            anchors.setdefault(identifier, position)
        if tag == 'a' and element.get('name'):
            anchors.setdefault(element.attrib['name'], position)
        semantic = element.get('{http://www.idpf.org/2007/ops}type', '').split()
        if tag in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}:
            title = clean_plain_text(' '.join(element.itertext()))
            if title:
                headings.append((position, title))
        elif 'chapter' in semantic or element.get('role') == 'doc-chapter':
            heading = next((child for child in element.iter() if child.tag.rsplit('}', 1)[-1] in {'h1', 'h2', 'h3'}), None)
            title = clean_plain_text(' '.join(heading.itertext())) if heading is not None else element.get('title')
            if title:
                headings.append((position, title))
        append(element.text)
        for child in element:
            walk(child)
            append(child.tail)
        if block:
            append('\n\n')

    walk(body)
    return ''.join(pieces), anchors, headings
