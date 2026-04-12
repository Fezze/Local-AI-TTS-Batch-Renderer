from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import lameenc
import numpy as np
import soundfile as sf

from .cli_audio_utils import (
    build_temp_part_base_name,
    compute_part_output_paths,
    create_audio_with_retry,
    cross_process_io_gate,
    extract_track_number,
    load_resume_state,
    safe_remove_path,
    save_resume_state,
    write_mp3_tags,
)
from .cli_chunking_utils import chunk_section
from .cli_models import AudioMetadata, Chunk, PartialRunComplete, DEFAULT_HEARTBEAT_SECONDS
from .cli_parsing import Chapter, get_group_leaf_title, sanitize_filename_component, slugify
from .cli_runtime import start_progress_heartbeat


class OutputPartWriter:
    def __init__(
        self,
        output_root: Path,
        base_output_dir: Path,
        part_index: int,
        multi_part: bool,
        sample_rate: int,
        force: bool,
        group_name: str | None = None,
        audio_metadata: AudioMetadata | None = None,
        mp3_only: bool = False,
        final_stem_override: str | None = None,
    ):
        self.part_index = part_index
        self.sample_rate = sample_rate
        self.group_name = group_name
        self.output_root = output_root
        self.base_output_dir = base_output_dir
        self.io_gate_lock_path = self.base_output_dir / ".local_tts_io.lock"
        self.multi_part = multi_part
        self.audio_metadata = audio_metadata
        self.mp3_only = mp3_only
        self.final_stem_override = final_stem_override
        self.base_name = build_temp_part_base_name(part_index=part_index, final_stem_override=final_stem_override)
        self.wav_path, self.mp3_path = compute_part_output_paths(output_root, base_output_dir, part_index, multi_part, self.base_name, group_name, final_stem_override)

        if not force and (self.wav_path.exists() or self.mp3_path.exists()):
            wav_cleared = safe_remove_path(self.wav_path) if self.wav_path.exists() else True
            mp3_cleared = safe_remove_path(self.mp3_path) if self.mp3_path.exists() else True
            if (not wav_cleared and self.wav_path.exists()) or (not mp3_cleared and self.mp3_path.exists()):
                raise FileExistsError(f"Output already exists for {self.wav_path.stem}. Use --force to overwrite.")

        if not self.mp3_only:
            self.wav_path.parent.mkdir(parents=True, exist_ok=True)
        self.mp3_path.parent.mkdir(parents=True, exist_ok=True)
        self.wav_file = None if self.mp3_only else sf.SoundFile(str(self.wav_path), mode="w", samplerate=sample_rate, channels=1, subtype="PCM_16")
        self.encoder = lameenc.Encoder()
        self.encoder.set_bit_rate(192)
        self.encoder.set_in_sample_rate(sample_rate)
        self.encoder.set_channels(1)
        self.encoder.set_quality(2)
        self.mp3_handle = self.mp3_path.open("wb")
        self.chapter_titles: list[str] = []
        self.start_chunk: int | None = None
        self.end_chunk: int | None = None
        self.samples_written = 0
        print(
            json.dumps(
                {
                    "part_open": True,
                    "part": self.part_index,
                    "group": self.group_name,
                    "mp3_path": str(self.mp3_path),
                }
            ),
            flush=True,
        )

    def write_audio(self, audio: np.ndarray) -> None:
        mono_audio = np.asarray(audio, dtype=np.float32)
        if self.wav_file is not None:
            self.wav_file.write(mono_audio)
        pcm = (np.clip(mono_audio, -1.0, 1.0) * 32767.0).astype(np.int16)
        self.mp3_handle.write(self.encoder.encode(pcm.tobytes()))
        self.samples_written += len(mono_audio)

    def close(self, force_numbered_first_part: bool = False) -> dict:
        self.mp3_handle.write(self.encoder.flush())
        self.mp3_handle.close()
        if self.wav_file is not None:
            self.wav_file.close()
        final_title = self.chapter_titles[0] if self.chapter_titles else get_group_leaf_title(self.group_name)
        final_base_name = sanitize_filename_component(self.final_stem_override or final_title)
        final_wav_path, final_mp3_path = compute_part_output_paths(
            self.output_root,
            self.base_output_dir,
            self.part_index,
            self.multi_part,
            final_base_name,
            self.group_name,
            self.final_stem_override,
            force_numbered_first_part=force_numbered_first_part,
        )
        with cross_process_io_gate(self.io_gate_lock_path):
            if not self.mp3_only and self.wav_path != final_wav_path:
                if final_wav_path.exists():
                    safe_remove_path(final_wav_path)
                self.wav_path.replace(final_wav_path)
            if self.mp3_path != final_mp3_path:
                if final_mp3_path.exists():
                    safe_remove_path(final_mp3_path)
                self.mp3_path.replace(final_mp3_path)
            self.wav_path = final_wav_path
            self.mp3_path = final_mp3_path
            if self.audio_metadata is not None:
                album_title = get_group_leaf_title(self.group_name) if self.group_name else self.audio_metadata.source_title
                track_number = extract_track_number(self.mp3_path.stem, self.part_index)
                write_mp3_tags(self.mp3_path, final_title, track_number, self.audio_metadata, album_title=album_title)
        part_payload = {
            "part": self.part_index,
            "wav_path": None if self.mp3_only else str(self.wav_path),
            "mp3_path": str(self.mp3_path),
            "duration_seconds": self.samples_written / self.sample_rate,
            "group": self.group_name,
            "chapter_titles": self.chapter_titles,
            "start_chunk": self.start_chunk,
            "end_chunk": self.end_chunk,
        }
        print(
            json.dumps(
                {
                    "part_close": True,
                    "part": self.part_index,
                    "group": self.group_name,
                    "start_chunk": self.start_chunk,
                    "end_chunk": self.end_chunk,
                    "duration_seconds": part_payload["duration_seconds"],
                    "mp3_path": str(self.mp3_path),
                }
            ),
            flush=True,
        )
        return part_payload


def _resolve_create_audio_callable():
    for module_name in ("local_tts_renderer.cli_core", "local_tts_renderer.cli"):
        module = sys.modules.get(module_name)
        if module is None:
            continue
        candidate = getattr(module, "create_audio_with_retry", None)
        if callable(candidate):
            return candidate
    return create_audio_with_retry


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

    create_audio = _resolve_create_audio_callable()
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
        },
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
) -> dict:
    manifest_root = output_root / final_stem_override if final_stem_override else output_root
    manifest_path = manifest_root.with_suffix(".json")
    checkpoint_path = manifest_root.with_suffix(".resume.json")
    if not force and manifest_path.exists():
        raise FileExistsError(f"Output already exists for {output_root.name}. Use --force to overwrite.")

    chunk_dir = manifest_root.parent / f"{manifest_root.name}-chunks"
    keep_chunks = keep_chunks and not mp3_only
    resume_state = None if force else load_resume_state(checkpoint_path)
    manifest_chunks: list[dict] = resume_state.get("manifest_chunks", []) if resume_state else []
    chapter_chunk_counts = [len(chunk_section(chapter.title, chapter.text, max_chars=max_chars, start_index=1)) for chapter in chapters]
    chapter_start_indices: list[int] = []
    next_start_index = 1
    for chunk_count in chapter_chunk_counts:
        chapter_start_indices.append(next_start_index)
        next_start_index += chunk_count
    total_chunks = next_start_index - 1
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
    heartbeat_stop, heartbeat_thread = start_progress_heartbeat(progress_state, heartbeat_seconds)
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
        resume_output_root = output_root
        if grouped_output and current_group:
            resume_output_root = output_root / group_dir_map.get(current_group, Path(slugify(current_group)))
        stale_base_name = build_temp_part_base_name(part_index=part_index, final_stem_override=final_stem_override)
        stale_wav, stale_mp3 = compute_part_output_paths(resume_output_root, base_output_dir, part_index, multi_part, stale_base_name, current_group, final_stem_override)
        for stale_path in (stale_wav, stale_mp3):
            if stale_path.exists():
                removed = safe_remove_path(stale_path)
                if not removed and stale_path.exists():
                    print(json.dumps({"resume_cleanup_warning": True, "path": str(stale_path)}), flush=True)
    try:
        for chapter_index, chapter in enumerate(chapters, start=1):
            if chapter_index < next_chapter_index:
                continue
            chapter_start_index = chapter_start_indices[chapter_index - 1]
            chapter_end_index = chapter_start_index + chapter_chunk_counts[chapter_index - 1] - 1
            chapter_chunks = chunk_section(chapter.title, chapter.text, max_chars=max_chars, start_index=chapter_start_index)
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
                    )
                current_group = chapter.group
                part_index = 1
                current_writer = None
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
                    )
                    current_writer = None
                    has_remaining_work = has_more_chunks_in_chapter or (chapter_index < total_chapters)
                    if max_parts_per_run > 0 and parts_closed_this_run >= max_parts_per_run and has_remaining_work:
                        print(json.dumps({"run_partial": True, "next_chapter_index": chapter_index if has_more_chunks_in_chapter else chapter_index + 1, "next_chunk_index": next_chunk_index, "next_part_index": part_index}), flush=True)
                        raise PartialRunComplete()
            next_chapter_index = chapter_index + 1
        if current_writer is not None:
            output_parts.append(current_writer.close())
    finally:
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
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if checkpoint_path.exists():
        checkpoint_path.unlink()
    return manifest
