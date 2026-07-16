from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from .atomic_io import write_json_atomic
from .cli_audio_utils import create_audio_with_retry
from .cli_chunking_utils import chunk_section
from .cli_models import AudioMetadata, Chunk, PartialRunComplete, DEFAULT_HEARTBEAT_SECONDS
from .cli_part_writer import OutputPartWriter
from .document_helpers import slugify
from .cli_render_cleanup import (
    list_render_artifacts,
    prepare_render_artifacts,
    remove_uncommitted_render_artifacts,
)
from .cli_resume import (
    build_resume_fingerprint,
    load_resume_state,
    output_artifact_paths,
    save_resume_state,
    validate_resume_plan,
)
from .cli_runtime import start_progress_heartbeat
from .sources.model import SourceChapter as Chapter

CREATE_AUDIO_WITH_RETRY = create_audio_with_retry


def render_chunk_audio(
    kokoro,
    chunk: Chunk,
    chapter: Chapter,
    chapter_index: int,
    total_chapters: int,
    position_in_chapter: int,
    total_chapter_chunks: int,
    voice: str,
    lang: str,
    trim_mode: str,
    speed: float,
    silence_ms: int,
    keep_chunks: bool,
    chunk_dir: Path,
    progress_state: dict,
    expected_sample_rate: int | None,
) -> tuple[np.ndarray, int, dict]:
    progress_state["completed_chunks"] += 1
    completed_chunks = progress_state["completed_chunks"]
    total_chunks = progress_state["total_chunks"]
    chunk_started_at = time.time()

    create_audio = CREATE_AUDIO_WITH_RETRY
    audio_parts, current_rate = create_audio(kokoro=kokoro, text=chunk.text, voice=voice, speed=speed, lang=lang, trim_mode=trim_mode)
    audio = np.concatenate(audio_parts)
    if expected_sample_rate is not None and current_rate != expected_sample_rate:
        raise RuntimeError(f"Sample rate changed from {expected_sample_rate} to {current_rate}.")

    if keep_chunks:
        chunk_dir.mkdir(parents=True, exist_ok=True)
        chunk_path = chunk_dir / f"{chunk.index:04d}.wav"
        sf.write(chunk_path, audio, current_rate)

    if silence_ms > 0 and position_in_chapter < total_chapter_chunks:
        silence = np.zeros(int(current_rate * silence_ms / 1000), dtype=np.float32)
        audio = np.concatenate([audio, silence])

    manifest_chunk = {
        "index": chunk.index,
        "heading": chunk.heading,
        "chapter": chapter.title,
        "chars": len(chunk.text),
        "text": chunk.text,
    }

    elapsed = progress_state["elapsed_offset"] + (time.time() - progress_state["started_at"])
    display_completed = min(completed_chunks, total_chunks) if total_chunks else completed_chunks
    avg_per_chunk = elapsed / completed_chunks if completed_chunks else 0.0
    eta_seconds = avg_per_chunk * max(total_chunks - display_completed, 0)
    chunk_elapsed = time.time() - chunk_started_at
    percent = (display_completed / total_chunks) * 100 if total_chunks else 100.0
    should_log = total_chunks <= 3 or position_in_chapter == 1 or position_in_chapter == total_chapter_chunks or chunk_elapsed >= 1.0
    if should_log:
        print(
            f"[{display_completed}/{total_chunks}] {percent:5.1f}% "
            f"chapter={chapter_index}/{total_chapters} chunk={chunk.index} chars={len(chunk.text)} "
            f"chunk_time={chunk_elapsed:.1f}s elapsed={elapsed:.1f}s eta={eta_seconds:.1f}s",
            flush=True,
        )
    return audio, current_rate, manifest_chunk


def save_safe_checkpoint(
    checkpoint_path: Path,
    next_chapter_index: int,
    next_chunk_index: int,
    completed_chunks: int,
    elapsed_seconds: float,
    sample_rate: int | None,
    output_parts: list[dict],
    manifest_chunks: list[dict],
    next_group: str | None,
    next_part_index: int,
    render_max_chars: int,
    fingerprint: str,
) -> None:
    save_resume_state(
        checkpoint_path,
        {
            "next_chapter_index": next_chapter_index,
            "next_chunk_index": next_chunk_index,
            "completed_chunks": completed_chunks,
            "elapsed_seconds": elapsed_seconds,
            "sample_rate": sample_rate,
            "output_parts": output_parts,
            "manifest_chunks": manifest_chunks,
            "next_group": next_group,
            "next_part_index": next_part_index,
            "render_max_chars": render_max_chars,
        },
        fingerprint=fingerprint,
    )


def render_audio(
    kokoro,
    chapters: list[Chapter],
    base_output_dir: Path,
    output_root: Path,
    group_dir_map: dict[str, Path],
    voice: str,
    lang: str,
    trim_mode: str,
    speed: float,
    max_chars: int,
    silence_ms: int,
    max_part_minutes: float,
    keep_chunks: bool,
    mp3_only: bool,
    force: bool,
    audio_metadata: AudioMetadata | None = None,
    heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
    final_stem_override: str | None = None,
    max_parts_per_run: int = 0,
    max_phoneme_chars: int = 0,
    fresh: bool = False,
    model_identity: dict[str, object] | str | None = None,
) -> dict:
    manifest_root = output_root / final_stem_override if final_stem_override else output_root
    manifest_path = manifest_root.with_suffix(".json")
    checkpoint_path = manifest_root.with_suffix(".resume.json")
    chunk_dir = manifest_root.parent / f"{manifest_root.name}-chunks"
    keep_chunks = keep_chunks and not mp3_only
    effective_max_chars = max_chars
    if max_phoneme_chars > 0:
        effective_max_chars = min(effective_max_chars, max_phoneme_chars)
    fingerprint = build_resume_fingerprint(
        chapters=chapters,
        voice=voice,
        lang=lang,
        trim_mode=trim_mode,
        speed=speed,
        max_chars=max_chars,
        max_phoneme_chars=max_phoneme_chars,
        silence_ms=silence_ms,
        max_part_minutes=max_part_minutes,
        keep_chunks=keep_chunks,
        mp3_only=mp3_only,
        audio_metadata=audio_metadata,
        group_dir_map=group_dir_map,
        final_stem_override=final_stem_override,
        model_identity=model_identity,
    )
    prepare_render_artifacts(
        base_output_dir=base_output_dir,
        output_root=output_root,
        manifest_path=manifest_path,
        checkpoint_path=checkpoint_path,
        chunk_dir=chunk_dir,
        final_stem_override=final_stem_override,
        expected_chapters=[(chapter.title, chapter.text) for chapter in chapters],
        force=force,
        fresh=fresh,
    )

    resume_state = load_resume_state(
        checkpoint_path,
        expected_fingerprint=fingerprint,
        expected_render_max_chars=max_chars,
    )
    manifest_chunks: list[dict] = resume_state.get("manifest_chunks", []) if resume_state else []
    chapter_chunk_plan: list[list[Chunk]] = []
    chapter_start_indices: list[int] = []
    planned_manifest_chunks: list[dict] = []
    planned_chapter_indices: list[int] = []
    next_start_index = 1
    for chapter_index, chapter in enumerate(chapters, start=1):
        chapter_start_indices.append(next_start_index)
        chunks = chunk_section(
            chapter.title,
            chapter.text,
            max_chars=effective_max_chars,
            start_index=next_start_index,
        )
        chapter_chunk_plan.append(chunks)
        for chunk in chunks:
            planned_manifest_chunks.append(
                {
                    "index": chunk.index,
                    "heading": chunk.heading,
                    "chapter": chapter.title,
                    "chars": len(chunk.text),
                    "text": chunk.text,
                }
            )
            planned_chapter_indices.append(chapter_index)
        next_start_index += len(chunks)
    chapter_chunk_counts = [len(chunks) for chunks in chapter_chunk_plan]
    total_chunks = next_start_index - 1
    if resume_state:
        validate_resume_plan(
            resume_state,
            checkpoint_path,
            planned_chunks=planned_manifest_chunks,
            planned_chapter_indices=planned_chapter_indices,
            chapter_count=len(chapters),
        )
    normalized_next_chapter_index = resume_state.get("next_chapter_index", 1) if resume_state else 1
    default_next_chunk_index = chapter_start_indices[normalized_next_chapter_index - 1] if chapters and 1 <= normalized_next_chapter_index <= len(chapters) else 1
    normalized_next_chunk_index = resume_state.get("next_chunk_index", default_next_chunk_index) if resume_state else 1
    normalized_completed_chunks = int(resume_state.get("completed_chunks", max(normalized_next_chunk_index - 1, 0))) if resume_state else 0
    progress_state = {
        "completed_chunks": min(normalized_completed_chunks, total_chunks),
        "total_chunks": total_chunks,
        "started_at": time.time(),
        "elapsed_offset": (resume_state.get("elapsed_seconds") or 0.0) if resume_state else 0.0,
        "chapter_index": None,
        "chapter_title": None,
    }
    sample_rate: int | None = resume_state.get("sample_rate") if resume_state else None
    max_part_samples: int | None = None
    current_writer: OutputPartWriter | None = None
    output_parts: list[dict] = resume_state.get("output_parts", []) if resume_state else []
    next_chunk_index = normalized_next_chunk_index if resume_state else 1
    part_index = resume_state.get("next_part_index", 1) if resume_state else 1
    parts_closed_this_run = 0
    total_chapters = len(chapters)
    multi_part = total_chapters > 1
    source_groups = [chapter.group for chapter in chapters if chapter.group]
    grouped_output = bool(source_groups)
    current_group: str | None = resume_state.get("next_group") if resume_state else None
    next_chapter_index = normalized_next_chapter_index if resume_state else 1
    if sample_rate is not None:
        max_part_samples = max(1, int(sample_rate * max_part_minutes * 60))
    if resume_state:
        print(json.dumps({"resume": True, "next_chapter_index": next_chapter_index, "next_part_index": part_index, "next_group": current_group}), flush=True)
        remove_uncommitted_render_artifacts(
            base_output_dir=base_output_dir,
            output_root=output_root,
            final_stem_override=final_stem_override,
            committed_files=output_artifact_paths(resume_state),
        )
    else:
        existing_artifacts = list_render_artifacts(
            base_output_dir=base_output_dir,
            output_root=output_root,
            final_stem_override=final_stem_override,
        )
        if existing_artifacts:
            raise FileExistsError(
                f"Unfinished output already exists for {output_root.name}. "
                "Use --fresh to discard it or --force to replace it."
            )
        save_safe_checkpoint(
            checkpoint_path,
            next_chapter_index=1,
            next_chunk_index=1,
            completed_chunks=0,
            elapsed_seconds=0.0,
            sample_rate=None,
            output_parts=[],
            manifest_chunks=[],
            next_group=None,
            next_part_index=1,
            render_max_chars=max_chars,
            fingerprint=fingerprint,
        )
    heartbeat_stop, heartbeat_thread = start_progress_heartbeat(progress_state, heartbeat_seconds)
    try:
        for chapter_index, chapter in enumerate(chapters, start=1):
            if chapter_index < next_chapter_index:
                continue
            chapter_start_index = chapter_start_indices[chapter_index - 1]
            chapter_end_index = chapter_start_index + chapter_chunk_counts[chapter_index - 1] - 1
            chapter_chunks = list(chapter_chunk_plan[chapter_index - 1])
            chapter_chunks = [chunk for chunk in chapter_chunks if chunk.index >= next_chunk_index]
            if not chapter_chunks:
                next_chunk_index = chapter_end_index + 1
                next_chapter_index = chapter_index + 1
                continue
            print(json.dumps({"chapter_dispatch": True, "chapter_index": chapter_index, "chapter_title": chapter.title, "group": chapter.group}), flush=True)
            progress_state["chapter_index"] = chapter_index
            progress_state["chapter_title"] = chapter.title
            if grouped_output and chapter.group != current_group:
                if current_writer is not None:
                    output_parts.append(current_writer.close())
                    save_safe_checkpoint(
                        checkpoint_path,
                        next_chapter_index=chapter_index,
                        next_chunk_index=chapter_chunks[0].index,
                        completed_chunks=progress_state["completed_chunks"],
                        elapsed_seconds=progress_state["elapsed_offset"] + (time.time() - progress_state["started_at"]),
                        sample_rate=sample_rate,
                        output_parts=output_parts,
                        manifest_chunks=manifest_chunks,
                        next_group=chapter.group,
                        next_part_index=1,
                        render_max_chars=max_chars,
                        fingerprint=fingerprint,
                    )
                current_group = chapter.group
                part_index = 1
                current_writer = None
                save_safe_checkpoint(
                    checkpoint_path,
                    next_chapter_index=chapter_index,
                    next_chunk_index=chapter_chunks[0].index,
                    completed_chunks=progress_state["completed_chunks"],
                    elapsed_seconds=progress_state["elapsed_offset"] + (time.time() - progress_state["started_at"]),
                    sample_rate=sample_rate,
                    output_parts=output_parts,
                    manifest_chunks=manifest_chunks,
                    next_group=current_group,
                    next_part_index=part_index,
                    render_max_chars=max_chars,
                    fingerprint=fingerprint,
                )
            print(json.dumps({"chapter_start": True, "chapter_index": chapter_index, "chapter_title": chapter.title, "chunk_count": len(chapter_chunks)}), flush=True)
            for position_in_chapter, chunk in enumerate(chapter_chunks, start=1):
                audio, current_rate, manifest_chunk = render_chunk_audio(
                    kokoro=kokoro,
                    chunk=chunk,
                    chapter=chapter,
                    chapter_index=chapter_index,
                    total_chapters=total_chapters,
                    position_in_chapter=position_in_chapter,
                    total_chapter_chunks=len(chapter_chunks),
                    voice=voice,
                    lang=lang,
                    trim_mode=trim_mode,
                    speed=speed,
                    silence_ms=silence_ms,
                    keep_chunks=keep_chunks,
                    chunk_dir=chunk_dir,
                    progress_state=progress_state,
                    expected_sample_rate=sample_rate,
                )
                if sample_rate is None:
                    sample_rate = current_rate
                    max_part_samples = max(1, int(sample_rate * max_part_minutes * 60))
                if current_writer is None:
                    current_output_root = output_root
                    if grouped_output and chapter.group:
                        current_output_root = output_root / group_dir_map.get(chapter.group, Path(slugify(chapter.group)))
                    current_writer = OutputPartWriter(
                        current_output_root,
                        base_output_dir,
                        part_index,
                        multi_part,
                        sample_rate,
                        force,
                        group_name=chapter.group,
                        audio_metadata=audio_metadata,
                        mp3_only=mp3_only,
                        final_stem_override=final_stem_override,
                    )
                if current_writer.start_chunk is None:
                    current_writer.start_chunk = chunk.index
                current_writer.end_chunk = chunk.index
                if not current_writer.chapter_titles or current_writer.chapter_titles[-1] != chapter.title:
                    current_writer.chapter_titles.append(chapter.title)
                current_writer.write_audio(audio)
                manifest_chunks.append(manifest_chunk)
                next_chunk_index = chunk.index + 1
                if max_part_samples is not None and current_writer.samples_written >= max_part_samples:
                    has_more_chunks_in_chapter = next_chunk_index <= chapter_end_index
                    output_parts.append(
                        current_writer.close(
                            force_numbered_first_part=(
                                bool(final_stem_override) and current_writer.part_index == 1 and has_more_chunks_in_chapter
                            )
                        )
                    )
                    parts_closed_this_run += 1
                    part_index += 1
                    save_safe_checkpoint(
                        checkpoint_path,
                        next_chapter_index=chapter_index if next_chunk_index <= chapter_end_index else chapter_index + 1,
                        next_chunk_index=next_chunk_index,
                        completed_chunks=progress_state["completed_chunks"],
                        elapsed_seconds=progress_state["elapsed_offset"] + (time.time() - progress_state["started_at"]),
                        sample_rate=sample_rate,
                        output_parts=output_parts,
                        manifest_chunks=manifest_chunks,
                        next_group=chapter.group,
                        next_part_index=part_index,
                        render_max_chars=max_chars,
                        fingerprint=fingerprint,
                    )
                    current_writer = None
                    has_remaining_work = has_more_chunks_in_chapter or (chapter_index < total_chapters)
                    if max_parts_per_run > 0 and parts_closed_this_run >= max_parts_per_run and has_remaining_work:
                        print(json.dumps({"run_partial": True, "next_chapter_index": chapter_index if has_more_chunks_in_chapter else chapter_index + 1, "next_chunk_index": next_chunk_index, "next_part_index": part_index}), flush=True)
                        raise PartialRunComplete()
            next_chapter_index = chapter_index + 1
        if current_writer is not None:
            output_parts.append(current_writer.close())
            current_writer = None
    finally:
        if current_writer is not None:
            current_writer.abort()
        heartbeat_stop.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=1.0)
    if sample_rate is None:
        raise RuntimeError("No audio was rendered.")

    manifest = {
        "source": str(output_root.name),
        "voice": voice,
        "lang": lang,
        "speed": speed,
        "sample_rate": sample_rate,
        "chapter_count": len(chapters),
        "chunk_count": len(manifest_chunks),
        "max_part_minutes": max_part_minutes,
        "parts": output_parts,
        "chunks": manifest_chunks,
    }
    write_json_atomic(manifest_path, manifest)
    if checkpoint_path.exists():
        checkpoint_path.unlink()
    return manifest
