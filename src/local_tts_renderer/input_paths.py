from __future__ import annotations

import glob
import hashlib
from pathlib import Path

from .document_helpers import slugify


def expand_pattern(item: str) -> list[Path]:
    if any(character in item for character in "*?[]"):
        return [Path(path) for path in sorted(glob.glob(item, recursive=True))]
    return [Path(item)]


def validate_source_outputs(inputs: list[Path], output_subdir: str | None = None) -> None:
    """Reject ambiguous ownership before any cache or output mutation."""
    identities: dict[str, list[Path]] = {}
    for path in dict.fromkeys(path.resolve() for path in inputs):
        key = (output_subdir or slugify(path.stem)).casefold()
        identities.setdefault(key, []).append(path)
    collisions = {key: paths for key, paths in identities.items() if len(paths) > 1}
    if collisions:
        details = "\n".join(f"  {key}: " + ", ".join(map(str, paths)) for key, paths in collisions.items())
        raise ValueError("Input output collision; use separate output directories or rename sources:\n" + details)


def source_cache_key(path: Path) -> str:
    identity = str(path.resolve()).encode("utf-8")
    return f"{slugify(path.stem)[:64]}-{hashlib.sha256(identity).hexdigest()}"
