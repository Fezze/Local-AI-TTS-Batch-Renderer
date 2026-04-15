from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from .cli_models import AudioMetadata
from .input_parsers import clean_plain_text


def strip_front_matter(text: str) -> str:
    return re.sub(r"\A---\s*\n.*?\n---\s*\n", "", text, flags=re.DOTALL)


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


__all__ = ["extract_epub_metadata", "strip_front_matter"]
