from __future__ import annotations

import hashlib
import json
import math
import shutil
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, is_dataclass
from pathlib import Path

from .atomic_io import atomic_write_text


RESUME_SCHEMA_VERSION = 2
FINGERPRINT_SCHEMA_VERSION = 1


class ResumeCheckpointError(RuntimeError):
    """A checkpoint cannot be resumed without risking mixed output."""


def _checkpoint_error(checkpoint_path: Path, detail: str) -> ResumeCheckpointError:
    return ResumeCheckpointError(
        f"Cannot resume from checkpoint '{checkpoint_path}': {detail} "
        "Run again with --fresh to discard this unfinished render safely."
    )


def _field(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _metadata_payload(metadata: object | None) -> dict[str, object] | None:
    if metadata is None:
        return None
    if is_dataclass(metadata) and not isinstance(metadata, type):
        raw: Mapping[str, object] = asdict(metadata)
    elif isinstance(metadata, Mapping):
        raw = metadata
    else:
        raw = {
            name: getattr(metadata, name, None)
            for name in ("source_title", "author", "publisher", "published_date", "language")
        }
    return {
        name: raw.get(name)
        for name in ("source_title", "author", "publisher", "published_date", "language")
    }


def _json_safe(value: object) -> object:
    if isinstance(value, Path):
        return value.as_posix()
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Unsupported fingerprint value: {type(value).__name__}")


def canonical_json_bytes(payload: Mapping[str, object]) -> bytes:
    """Return a stable UTF-8 representation used only for identity hashing."""

    normalized = _json_safe(payload)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def build_resume_fingerprint(
    *,
    chapters: Sequence[object],
    voice: str,
    lang: str,
    trim_mode: str,
    speed: float,
    max_chars: int,
    max_phoneme_chars: int,
    silence_ms: int,
    max_part_minutes: float,
    keep_chunks: bool,
    mp3_only: bool,
    audio_metadata: object | None = None,
    group_dir_map: Mapping[str, Path | str] | None = None,
    final_stem_override: str | None = None,
    model_identity: Mapping[str, object] | str | None = None,
) -> str:
    """Hash normalized render input and only output-affecting configuration."""

    payload = {
        "fingerprint_schema_version": FINGERPRINT_SCHEMA_VERSION,
        "source": [
            {
                "title": _field(chapter, "title"),
                "text": _field(chapter, "text"),
                "group": _field(chapter, "group"),
            }
            for chapter in chapters
        ],
        "configuration": {
            "voice": voice,
            "lang": lang,
            "trim_mode": trim_mode,
            "speed": speed,
            "max_chars": max_chars,
            "max_phoneme_chars": max_phoneme_chars,
            "silence_ms": silence_ms,
            "max_part_minutes": max_part_minutes,
            "keep_chunks": keep_chunks,
            "mp3_only": mp3_only,
            "audio_metadata": _metadata_payload(audio_metadata),
            "group_dir_map": dict(group_dir_map or {}),
            "final_stem_override": final_stem_override,
            "model_identity": model_identity,
        },
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _is_int(value: object, *, minimum: int) -> bool:
    return type(value) is int and value >= minimum


def _is_number(value: object, *, minimum: float) -> bool:
    return type(value) in (int, float) and math.isfinite(float(value)) and float(value) >= minimum


def _validate_fingerprint(value: object, checkpoint_path: Path) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdefABCDEF" for character in value)
    ):
        raise _checkpoint_error(checkpoint_path, "the fingerprint is missing or malformed.")
    return value.lower()


def _validate_output_parts(parts: object, checkpoint_path: Path, validate_artifacts: bool) -> list[dict]:
    if not isinstance(parts, list):
        raise _checkpoint_error(checkpoint_path, "'output_parts' must be a list.")
    seen_artifacts: set[Path] = set()
    for index, part in enumerate(parts):
        if not isinstance(part, dict):
            raise _checkpoint_error(checkpoint_path, f"output part {index + 1} is not an object.")
        if not _is_int(part.get("part"), minimum=1):
            raise _checkpoint_error(checkpoint_path, f"output part {index + 1} has an invalid part number.")
        mp3_path = part.get("mp3_path")
        wav_path = part.get("wav_path")
        if not isinstance(mp3_path, str) or not mp3_path:
            raise _checkpoint_error(checkpoint_path, f"output part {index + 1} has no MP3 path.")
        if wav_path is not None and (not isinstance(wav_path, str) or not wav_path):
            raise _checkpoint_error(checkpoint_path, f"output part {index + 1} has an invalid WAV path.")
        if not _is_int(part.get("start_chunk"), minimum=1):
            raise _checkpoint_error(checkpoint_path, f"output part {index + 1} has an invalid start chunk.")
        if not _is_int(part.get("end_chunk"), minimum=part["start_chunk"]):
            raise _checkpoint_error(checkpoint_path, f"output part {index + 1} has an invalid end chunk.")
        if part.get("group") is not None and not isinstance(part.get("group"), str):
            raise _checkpoint_error(checkpoint_path, f"output part {index + 1} has an invalid group.")
        for value in (mp3_path, wav_path):
            if value is None:
                continue
            artifact = Path(value)
            if not artifact.is_absolute():
                artifact = checkpoint_path.parent / artifact
            try:
                artifact_identity = artifact.resolve(strict=False)
            except (OSError, RuntimeError) as exc:
                raise _checkpoint_error(
                    checkpoint_path,
                    f"output part {index + 1} has an invalid artifact path: {artifact}.",
                ) from exc
            if artifact_identity in seen_artifacts:
                raise _checkpoint_error(
                    checkpoint_path,
                    f"output artifact is referenced more than once: {artifact}.",
                )
            seen_artifacts.add(artifact_identity)
            if validate_artifacts:
                if not artifact.is_file() or artifact.stat().st_size == 0:
                    raise _checkpoint_error(checkpoint_path, f"committed output artifact is missing or empty: {artifact}.")
    return parts


def validate_resume_state(
    state: object,
    checkpoint_path: Path,
    *,
    expected_fingerprint: str,
    expected_render_max_chars: int,
    validate_artifacts: bool = True,
) -> dict:
    if not isinstance(state, dict):
        raise _checkpoint_error(checkpoint_path, "the JSON root must be an object.")
    if state.get("schema_version") != RESUME_SCHEMA_VERSION:
        raise _checkpoint_error(checkpoint_path, f"unsupported schema version {state.get('schema_version')!r}.")

    stored_fingerprint = _validate_fingerprint(state.get("fingerprint"), checkpoint_path)
    normalized_expected = _validate_fingerprint(expected_fingerprint, checkpoint_path)
    if stored_fingerprint != normalized_expected:
        raise _checkpoint_error(checkpoint_path, "the source or rendering configuration has changed.")

    integer_fields = {
        "next_chapter_index": 1,
        "next_chunk_index": 1,
        "completed_chunks": 0,
        "next_part_index": 1,
        "render_max_chars": 1,
    }
    for name, minimum in integer_fields.items():
        if not _is_int(state.get(name), minimum=minimum):
            raise _checkpoint_error(checkpoint_path, f"'{name}' is invalid.")
    if state["render_max_chars"] != expected_render_max_chars:
        raise _checkpoint_error(checkpoint_path, "the pinned chunk size has changed.")
    if not _is_number(state.get("elapsed_seconds"), minimum=0.0):
        raise _checkpoint_error(checkpoint_path, "'elapsed_seconds' is invalid.")
    sample_rate = state.get("sample_rate")
    if sample_rate is not None and not _is_int(sample_rate, minimum=1):
        raise _checkpoint_error(checkpoint_path, "'sample_rate' is invalid.")
    if state.get("next_group") is not None and not isinstance(state.get("next_group"), str):
        raise _checkpoint_error(checkpoint_path, "'next_group' is invalid.")

    parts = _validate_output_parts(state.get("output_parts"), checkpoint_path, validate_artifacts)
    manifest_chunks = state.get("manifest_chunks")
    if not isinstance(manifest_chunks, list) or any(not isinstance(chunk, dict) for chunk in manifest_chunks):
        raise _checkpoint_error(checkpoint_path, "'manifest_chunks' must be a list of objects.")
    completed_chunks = state["completed_chunks"]
    if len(manifest_chunks) != completed_chunks:
        raise _checkpoint_error(checkpoint_path, "completed chunk count does not match the checkpoint manifest.")
    if state["next_chunk_index"] != completed_chunks + 1:
        raise _checkpoint_error(checkpoint_path, "the next chunk index is inconsistent with completed work.")
    if completed_chunks > 0 and sample_rate is None:
        raise _checkpoint_error(checkpoint_path, "completed work has no sample rate.")
    if completed_chunks == 0 and parts:
        raise _checkpoint_error(checkpoint_path, "output parts exist without completed chunks.")
    if completed_chunks > 0 and not parts:
        raise _checkpoint_error(checkpoint_path, "completed chunks have no committed output parts.")
    expected_start_chunk = 1
    previous_part: dict | None = None
    for index, part in enumerate(parts, start=1):
        if part["start_chunk"] != expected_start_chunk:
            raise _checkpoint_error(checkpoint_path, f"output part {index} does not continue the committed chunk range.")
        expected_part_number = 1
        if previous_part is not None and previous_part.get("group") == part.get("group"):
            expected_part_number = previous_part["part"] + 1
        if part["part"] != expected_part_number:
            raise _checkpoint_error(checkpoint_path, f"output part {index} has an inconsistent part number.")
        expected_start_chunk = part["end_chunk"] + 1
        previous_part = part
    if expected_start_chunk != completed_chunks + 1:
        raise _checkpoint_error(checkpoint_path, "committed output parts do not cover all completed chunks.")
    expected_next_part = 1
    if parts and parts[-1].get("group") == state.get("next_group"):
        expected_next_part = parts[-1]["part"] + 1
    if state["next_part_index"] != expected_next_part:
        raise _checkpoint_error(checkpoint_path, "the next part index is inconsistent with committed output.")
    return state


def validate_resume_plan(
    state: Mapping[str, object],
    checkpoint_path: Path,
    *,
    planned_chunks: Sequence[Mapping[str, object]],
    planned_chapter_indices: Sequence[int],
    chapter_count: int,
) -> None:
    """Ensure checkpoint progress is an exact prefix of the current chunk plan."""

    if len(planned_chunks) != len(planned_chapter_indices):
        raise ValueError("planned chunks and chapter indices must have equal lengths")
    completed_chunks = int(state["completed_chunks"])
    if completed_chunks > len(planned_chunks):
        raise _checkpoint_error(checkpoint_path, "completed work exceeds the current chunk plan.")

    stored_chunks = state["manifest_chunks"]
    manifest_keys = ("index", "heading", "chapter", "chars", "text")
    for position, (stored, expected) in enumerate(
        zip(stored_chunks, planned_chunks[:completed_chunks], strict=True),
        start=1,
    ):
        if any(stored.get(key) != expected.get(key) for key in manifest_keys):
            raise _checkpoint_error(
                checkpoint_path,
                f"completed chunk {position} does not match the current render plan.",
            )

    if completed_chunks < len(planned_chunks):
        expected_chapter = planned_chapter_indices[completed_chunks]
        expected_chunk = planned_chunks[completed_chunks].get("index")
        if state["next_chunk_index"] != expected_chunk:
            raise _checkpoint_error(checkpoint_path, "the next chunk does not match the current render plan.")
    else:
        expected_chapter = chapter_count + 1 if chapter_count else 1
    if state["next_chapter_index"] != expected_chapter:
        raise _checkpoint_error(checkpoint_path, "the next chapter does not match the current render plan.")


def _legacy_is_empty(state: Mapping[str, object]) -> bool:
    return (
        state.get("completed_chunks", 0) == 0
        and not state.get("output_parts", [])
        and not state.get("manifest_chunks", [])
        and state.get("next_chapter_index", 1) == 1
        and state.get("next_chunk_index", 1) == 1
        and state.get("next_part_index", 1) == 1
        and state.get("sample_rate") is None
    )


def _upgrade_empty_legacy(
    state: Mapping[str, object],
    fingerprint: str,
    render_max_chars: int,
) -> dict:
    elapsed_seconds = state.get("elapsed_seconds", 0.0)
    if elapsed_seconds is None:
        elapsed_seconds = 0.0
    return {
        "schema_version": RESUME_SCHEMA_VERSION,
        "fingerprint": fingerprint,
        "next_chapter_index": 1,
        "next_chunk_index": 1,
        "completed_chunks": 0,
        "elapsed_seconds": elapsed_seconds,
        "sample_rate": None,
        "output_parts": [],
        "manifest_chunks": [],
        "next_group": state.get("next_group"),
        "next_part_index": 1,
        "render_max_chars": render_max_chars,
    }


def save_resume_state(checkpoint_path: Path, state: Mapping[str, object], *, fingerprint: str | None = None) -> dict:
    """Validate and atomically replace a version-2 checkpoint."""

    checkpoint_path = Path(checkpoint_path)
    payload = dict(state)
    payload["schema_version"] = RESUME_SCHEMA_VERSION
    if fingerprint is not None:
        payload["fingerprint"] = fingerprint
    expected_fingerprint = _validate_fingerprint(payload.get("fingerprint"), checkpoint_path)
    validate_resume_state(
        payload,
        checkpoint_path,
        expected_fingerprint=expected_fingerprint,
        expected_render_max_chars=payload["render_max_chars"],
        validate_artifacts=False,
    )
    try:
        serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n"
    except (TypeError, ValueError) as exc:
        raise ResumeCheckpointError(f"Cannot serialize resume checkpoint '{checkpoint_path}': {exc}") from exc
    atomic_write_text(checkpoint_path, serialized)
    return payload


def load_resume_state(
    checkpoint_path: Path,
    *,
    expected_fingerprint: str,
    expected_render_max_chars: int,
    validate_artifacts: bool = True,
) -> dict | None:
    """Load v2 state; upgrade only a legacy checkpoint with no committed work."""

    checkpoint_path = Path(checkpoint_path)
    if not checkpoint_path.exists():
        return None
    try:
        state = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _checkpoint_error(checkpoint_path, f"the JSON is unreadable or corrupt ({exc}).") from exc
    if not isinstance(state, dict):
        raise _checkpoint_error(checkpoint_path, "the JSON root must be an object.")

    if "schema_version" not in state:
        if not _legacy_is_empty(state):
            raise _checkpoint_error(
                checkpoint_path,
                "this legacy checkpoint contains completed work and cannot be verified against current settings.",
            )
        state = _upgrade_empty_legacy(
            state,
            _validate_fingerprint(expected_fingerprint, checkpoint_path),
            expected_render_max_chars,
        )
        save_resume_state(checkpoint_path, state)

    return validate_resume_state(
        state,
        checkpoint_path,
        expected_fingerprint=expected_fingerprint,
        expected_render_max_chars=expected_render_max_chars,
        validate_artifacts=validate_artifacts,
    )


def output_artifact_paths(state: Mapping[str, object] | None) -> tuple[Path, ...]:
    paths: list[Path] = []
    if state is None:
        return ()
    parts = state.get("output_parts", [])
    if not isinstance(parts, list):
        return ()
    for part in parts:
        if not isinstance(part, Mapping):
            continue
        for key in ("wav_path", "mp3_path"):
            value = part.get(key)
            if isinstance(value, str) and value:
                paths.append(Path(value))
    return tuple(paths)


def _inside_root(path: Path, allowed_root: Path) -> Path:
    candidate = path if path.is_absolute() else allowed_root / path
    resolved = candidate.resolve(strict=False)
    root = allowed_root.resolve(strict=False)
    if resolved == root or root not in resolved.parents:
        raise ResumeCheckpointError(f"Refusing to remove path outside the output root: {path}")
    return candidate


def remove_exact_artifacts(
    *,
    files: Iterable[Path | str] = (),
    directories: Iterable[Path | str] = (),
    allowed_root: Path,
) -> tuple[Path, ...]:
    """Remove only caller-enumerated paths, after validating the whole set."""

    checked_files = list(dict.fromkeys(_inside_root(Path(path), allowed_root) for path in files))
    checked_directories = list(dict.fromkeys(_inside_root(Path(path), allowed_root) for path in directories))
    for path in checked_files:
        if path.exists() and path.is_dir() and not path.is_symlink():
            raise ResumeCheckpointError(f"Refusing to unlink directory as a file: {path}")
    for path in checked_directories:
        if path.exists() and not path.is_dir() and not path.is_symlink():
            raise ResumeCheckpointError(f"Refusing to remove file as a directory: {path}")

    removed: list[Path] = []
    for path in checked_files:
        if path.exists() or path.is_symlink():
            path.unlink()
            removed.append(path)
    for path in checked_directories:
        if path.is_symlink():
            path.unlink()
            removed.append(path)
        elif path.exists():
            shutil.rmtree(path)
            removed.append(path)
    return tuple(removed)


__all__ = [
    "FINGERPRINT_SCHEMA_VERSION",
    "RESUME_SCHEMA_VERSION",
    "ResumeCheckpointError",
    "build_resume_fingerprint",
    "canonical_json_bytes",
    "load_resume_state",
    "output_artifact_paths",
    "remove_exact_artifacts",
    "save_resume_state",
    "validate_resume_plan",
    "validate_resume_state",
]
