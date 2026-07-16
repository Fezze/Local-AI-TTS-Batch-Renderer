from __future__ import annotations

import json
import re
from pathlib import Path
from collections.abc import Iterable, Sequence

from .cli_audio_utils import build_temp_part_base_name, compute_part_output_paths
from .cli_resume import ResumeCheckpointError, remove_exact_artifacts
from .document_helpers import sanitize_filename_component


def read_untrusted_resume_state(checkpoint_path: Path) -> dict | None:
    if not checkpoint_path.exists():
        return None
    try:
        value = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def pending_part_paths(
    *,
    output_root: Path,
    base_output_dir: Path,
    part_index: int,
    multi_part: bool,
    group_name: str | None,
    final_stem_override: str | None,
) -> tuple[Path, Path]:
    base_name = build_temp_part_base_name(
        part_index=part_index,
        final_stem_override=final_stem_override,
    )
    return compute_part_output_paths(
        output_root,
        base_output_dir,
        part_index,
        multi_part,
        base_name,
        group_name,
        final_stem_override,
    )


def _owned_stem(stem: str, final_stem_override: str) -> bool:
    normalized = sanitize_filename_component(final_stem_override)
    if stem in {final_stem_override, normalized}:
        return True
    chapter_match = re.match(r"^(\d+)\s*-\s*(.+)$", normalized)
    if chapter_match:
        chapter_number, title = chapter_match.groups()
        if re.fullmatch(rf"{re.escape(chapter_number)}-\d+ - {re.escape(title.strip())}", stem):
            return True
    elif re.fullmatch(rf"\d+-{re.escape(normalized)}", stem):
        return True
    return bool(re.fullmatch(rf"\d+-tmp-{re.escape(normalized)}-part-\d+", stem))


def _artifact_roots(
    base_output_dir: Path,
    output_root: Path,
) -> tuple[Path, Path]:
    relative_root = output_root.relative_to(base_output_dir)
    return (
        base_output_dir / "wav" / relative_root,
        base_output_dir / "mp3" / relative_root,
    )


def list_render_artifacts(
    *,
    base_output_dir: Path,
    output_root: Path,
    final_stem_override: str | None,
) -> tuple[Path, ...]:
    """List audio paths owned by one render job, based only on its output identity."""

    owned: list[Path] = []
    for directory, suffix in zip(
        _artifact_roots(base_output_dir, output_root),
        (".wav", ".mp3"),
        strict=True,
    ):
        if not directory.exists():
            continue
        candidates = directory.rglob(f"*{suffix}") if final_stem_override is None else directory.iterdir()
        for path in candidates:
            if path.suffix.lower() != suffix or not (path.is_file() or path.is_symlink()):
                continue
            if final_stem_override is None or _owned_stem(path.stem, final_stem_override):
                owned.append(path)
    return tuple(owned)


def load_complete_manifest(
    *,
    manifest_path: Path,
    base_output_dir: Path,
    output_root: Path,
    final_stem_override: str | None,
    expected_chapters: Sequence[tuple[str, str]],
) -> dict | None:
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    parts = value.get("parts")
    chunks = value.get("chunks")
    if not isinstance(parts, list) or not parts or not isinstance(chunks, list) or not chunks:
        return None
    if value.get("chapter_count") != len(expected_chapters) or value.get("chunk_count") != len(chunks):
        return None
    actual_chapter_runs: list[tuple[str, str]] = []
    for index, chunk in enumerate(chunks, start=1):
        if not isinstance(chunk, dict) or chunk.get("index") != index:
            return None
        chapter = chunk.get("chapter")
        text = chunk.get("text")
        if not isinstance(chapter, str) or not isinstance(text, str) or chunk.get("chars") != len(text):
            return None
        normalized_text = re.sub(r"\s+", " ", text).strip()
        if not normalized_text:
            return None
        if actual_chapter_runs and actual_chapter_runs[-1][0] == chapter:
            previous_title, previous_text = actual_chapter_runs[-1]
            actual_chapter_runs[-1] = (previous_title, f"{previous_text} {normalized_text}")
        else:
            actual_chapter_runs.append((chapter, normalized_text))

    expected_chapter_runs: list[tuple[str, str]] = []
    for chapter, text in expected_chapters:
        normalized_text = re.sub(r"\s+", " ", text).strip()
        if not normalized_text:
            continue
        if expected_chapter_runs and expected_chapter_runs[-1][0] == chapter:
            previous_title, previous_text = expected_chapter_runs[-1]
            expected_chapter_runs[-1] = (previous_title, f"{previous_text} {normalized_text}")
        else:
            expected_chapter_runs.append((chapter, normalized_text))
    if actual_chapter_runs != expected_chapter_runs:
        return None

    expected_start_chunk = 1
    previous_part: dict | None = None
    for part in parts:
        if not isinstance(part, dict):
            return None
        start_chunk = part.get("start_chunk")
        end_chunk = part.get("end_chunk")
        part_number = part.get("part")
        group = part.get("group")
        if (
            type(start_chunk) is not int
            or start_chunk != expected_start_chunk
            or type(end_chunk) is not int
            or end_chunk < start_chunk
            or type(part_number) is not int
            or part_number < 1
            or (group is not None and not isinstance(group, str))
        ):
            return None
        expected_part_number = 1
        if previous_part is not None and previous_part.get("group") == group:
            expected_part_number = previous_part["part"] + 1
        if part_number != expected_part_number:
            return None
        expected_start_chunk = end_chunk + 1
        previous_part = part
    if expected_start_chunk != len(chunks) + 1:
        return None

    owned = {
        path.resolve(strict=False)
        for path in list_render_artifacts(
            base_output_dir=base_output_dir,
            output_root=output_root,
            final_stem_override=final_stem_override,
        )
    }
    seen_artifacts: set[Path] = set()
    try:
        for part in parts:
            for key in ("mp3_path", "wav_path"):
                raw_path = part.get(key)
                if raw_path is None and key == "wav_path":
                    continue
                if not isinstance(raw_path, str) or not raw_path:
                    return None
                path = Path(raw_path)
                candidate = path if path.is_absolute() else base_output_dir / path
                resolved = candidate.resolve(strict=False)
                if (
                    resolved in seen_artifacts
                    or resolved not in owned
                    or not candidate.is_file()
                    or candidate.stat().st_size == 0
                ):
                    return None
                seen_artifacts.add(resolved)
    except OSError:
        return None
    return value


def remove_uncommitted_render_artifacts(
    *,
    base_output_dir: Path,
    output_root: Path,
    final_stem_override: str | None,
    committed_files: Iterable[Path | str],
) -> tuple[Path, ...]:
    committed: set[Path] = set()
    for value in committed_files:
        path = Path(value)
        candidate = path if path.is_absolute() else base_output_dir / path
        committed.add(candidate.resolve(strict=False))
    owned = list_render_artifacts(
        base_output_dir=base_output_dir,
        output_root=output_root,
        final_stem_override=final_stem_override,
    )
    owned_resolved = {path.resolve(strict=False) for path in owned}
    if not committed.issubset(owned_resolved):
        raise ResumeCheckpointError(
            "Checkpoint contains an output path that is not owned by this render job. "
            "Run again with --fresh to discard it safely."
        )
    uncommitted = [
        path
        for path in owned
        if path.resolve(strict=False) not in committed
    ]
    return remove_exact_artifacts(files=uncommitted, allowed_root=base_output_dir)


def reset_render_artifacts(
    *,
    base_output_dir: Path,
    output_root: Path,
    manifest_path: Path,
    checkpoint_path: Path,
    chunk_dir: Path,
    final_stem_override: str | None,
    include_manifest: bool,
) -> tuple[Path, ...]:
    """Remove artifacts owned by exactly one render job before a fresh run."""

    files: list[Path] = [checkpoint_path]
    if include_manifest:
        files.append(manifest_path)

    wav_root, mp3_root = _artifact_roots(base_output_dir, output_root)
    directories: list[Path] = [chunk_dir]
    if final_stem_override is None:
        directories.extend((wav_root, mp3_root))
    else:
        files.extend(
            list_render_artifacts(
                base_output_dir=base_output_dir,
                output_root=output_root,
                final_stem_override=final_stem_override,
            )
        )

    return remove_exact_artifacts(
        files=files,
        directories=directories,
        allowed_root=base_output_dir,
    )


def prepare_render_artifacts(
    *,
    base_output_dir: Path,
    output_root: Path,
    manifest_path: Path,
    checkpoint_path: Path,
    chunk_dir: Path,
    final_stem_override: str | None,
    expected_chapters: Sequence[tuple[str, str]],
    force: bool,
    fresh: bool,
) -> None:
    """Resolve completed, resumable, and abandoned output before rendering."""

    completed_manifest = (
        load_complete_manifest(
            manifest_path=manifest_path,
            base_output_dir=base_output_dir,
            output_root=output_root,
            final_stem_override=final_stem_override,
            expected_chapters=expected_chapters,
        )
        if manifest_path.exists()
        else None
    )
    if completed_manifest is not None:
        if not force:
            raise FileExistsError(f"Output already exists for {output_root.name}. Use --force to overwrite.")
        include_manifest = True
    elif manifest_path.exists():
        if checkpoint_path.exists() and not fresh:
            return
        if not (fresh or force):
            raise FileExistsError(
                f"Incomplete output manifest exists for {output_root.name}. "
                "Use --fresh to discard it or --force to replace it."
            )
        include_manifest = True
    elif fresh or (force and not checkpoint_path.exists()):
        include_manifest = False
    else:
        return

    reset_render_artifacts(
        base_output_dir=base_output_dir,
        output_root=output_root,
        manifest_path=manifest_path,
        checkpoint_path=checkpoint_path,
        chunk_dir=chunk_dir,
        final_stem_override=final_stem_override,
        include_manifest=include_manifest,
    )


__all__ = [
    "list_render_artifacts",
    "load_complete_manifest",
    "pending_part_paths",
    "prepare_render_artifacts",
    "read_untrusted_resume_state",
    "remove_uncommitted_render_artifacts",
    "reset_render_artifacts",
]
