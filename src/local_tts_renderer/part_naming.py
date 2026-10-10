"""Shared chapter/part numbering for planned work and rendered audio."""
import re

from .document_helpers import sanitize_filename_component


def split_numbered_title(stem: str) -> tuple[str, str] | None:
    match = re.match(r"^(\d+(?:\s*-\s*\d+)*)\s*-\s*(.+)$", stem)
    if not match:
        return None
    number, title = match.groups()
    return re.sub(r"\s+", "", number), title


def numbered_part_stem(stem: str, index: int) -> str:
    normalized = sanitize_filename_component(stem)
    numbered = split_numbered_title(normalized)
    if numbered:
        number, title = numbered
        return f"{number}-{index:02d} - {title.strip()}"
    return f"{index:02d}-{normalized}"
