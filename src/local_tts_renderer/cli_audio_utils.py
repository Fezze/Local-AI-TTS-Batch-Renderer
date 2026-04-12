from __future__ import annotations

import contextlib
import json
import os
import re
import time
from pathlib import Path

import lameenc
import numpy as np
import soundfile as sf
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, COMM, ID3NoHeaderError, TPUB

from .cli_chunking_utils import split_text_for_retry
from .cli_models import AudioMetadata
from .cli_parsing import get_group_leaf_title, sanitize_filename_component


def light_trim_audio(samples: np.ndarray, sample_rate: int, threshold: float = 0.003, padding_ms: int = 40) -> np.ndarray:
    audio = np.asarray(samples, dtype=np.float32)
    if audio.size == 0:
        return audio
    non_silent = np.flatnonzero(np.abs(audio) > threshold)
    if non_silent.size == 0:
        return audio
    padding = int(sample_rate * padding_ms / 1000)
    start = max(int(non_silent[0]) - padding, 0)
    end = min(int(non_silent[-1]) + padding + 1, len(audio))
    return audio[start:end]


def create_audio_with_retry(
    kokoro,
    text: str,
    voice: str,
    speed: float,
    lang: str,
    trim_mode: str,
    depth: int = 0,
) -> tuple[list[np.ndarray], int]:
    try:
        use_full_trim = trim_mode == "full"
        samples, sample_rate = kokoro.create(text, voice=voice, speed=speed, lang=lang, trim=use_full_trim)
        normalized = np.asarray(samples, dtype=np.float32)
        if trim_mode == "light":
            normalized = light_trim_audio(normalized, sample_rate)
        return [normalized], sample_rate
    except Exception as exc:
        message = str(exc).lower()
        exc_name = type(exc).__name__.lower()
        retryable = (
            isinstance(exc, IndexError)
            or "bad allocation" in message
            or "onnxruntimeerror" in message
            or "arraymemoryerror" in exc_name
            or "unable to allocate" in message
        )
        if not retryable or depth >= 8 or len(text.strip()) < 20:
            raise

        parts = [part for part in split_text_for_retry(text) if part]
        if len(parts) < 2:
            raise

        combined_parts: list[np.ndarray] = []
        sample_rate: int | None = None
        for part in parts:
            sub_parts, current_rate = create_audio_with_retry(
                kokoro=kokoro,
                text=part,
                voice=voice,
                speed=speed,
                lang=lang,
                trim_mode=trim_mode,
                depth=depth + 1,
            )
            if sample_rate is None:
                sample_rate = current_rate
            elif current_rate != sample_rate:
                raise RuntimeError(f"Sample rate changed from {sample_rate} to {current_rate}.")
            combined_parts.extend(sub_parts)

        if sample_rate is None:
            raise RuntimeError("Retry split produced no audio.")
        return combined_parts, sample_rate


def load_resume_state(checkpoint_path: Path) -> dict | None:
    if not checkpoint_path.exists():
        return None
    return json.loads(checkpoint_path.read_text(encoding="utf-8"))


def save_resume_state(checkpoint_path: Path, state: dict) -> None:
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def remove_output_files(part: dict) -> None:
    for key in ("wav_path", "mp3_path"):
        value = part.get(key)
        if not value:
            continue
        path = Path(value)
        if path.exists():
            path.unlink()


def build_temp_part_base_name(part_index: int, final_stem_override: str | None) -> str:
    if final_stem_override:
        safe_stem = sanitize_filename_component(final_stem_override)
        return f"tmp-{safe_stem}-part-{part_index:02d}"
    return f"_part-{part_index:02d}"


def safe_remove_path(path: Path, retries: int = 6, delay_seconds: float = 0.25) -> bool:
    for attempt in range(retries):
        try:
            path.unlink(missing_ok=True)
            return True
        except FileNotFoundError:
            return True
        except PermissionError:
            if not path.exists():
                return True
            if attempt == retries - 1:
                break
            time.sleep(delay_seconds * (attempt + 1))
        except OSError:
            if not path.exists():
                return True
            if attempt == retries - 1:
                break
            time.sleep(delay_seconds * (attempt + 1))
    return not path.exists()


def compute_part_output_paths(
    output_root: Path,
    base_output_dir: Path,
    part_index: int,
    multi_part: bool,
    base_name: str,
    group_name: str | None,
    final_stem_override: str | None = None,
    force_numbered_first_part: bool = False,
) -> tuple[Path, Path]:
    def normalize_stem(stem: str) -> str:
        sanitized = sanitize_filename_component(stem)
        return re.sub(r"\s*-\s*", "-", sanitized).strip()

    relative_root = output_root.relative_to(base_output_dir)
    wav_dir = base_output_dir / "wav" / relative_root
    mp3_dir = base_output_dir / "mp3" / relative_root

    if not multi_part and part_index == 1:
        if force_numbered_first_part and final_stem_override:
            normalized = sanitize_filename_component(final_stem_override)
            chapter_match = re.match(r"^(\d+)\s*-\s*(.+)$", normalized)
            if chapter_match:
                chapter_no, chapter_rest = chapter_match.groups()
                chapter_part_name = f"{chapter_no}-01 - {chapter_rest.strip()}"
                return wav_dir / f"{chapter_part_name}.wav", mp3_dir / f"{chapter_part_name}.mp3"
        final_name = final_stem_override or (relative_root.name if group_name is None else base_name)
        return wav_dir / f"{final_name}.wav", mp3_dir / f"{final_name}.mp3"
    if final_stem_override and normalize_stem(base_name) == normalize_stem(final_stem_override):
        chapter_match = re.match(r"^(\d+)\s*-\s*(.+)$", normalize_stem(base_name))
        if chapter_match:
            chapter_no, chapter_rest = chapter_match.groups()
            chapter_part_name = f"{chapter_no}-{part_index:02d} - {chapter_rest.strip()}"
            return wav_dir / f"{chapter_part_name}.wav", mp3_dir / f"{chapter_part_name}.mp3"
    return wav_dir / f"{part_index:02d}-{base_name}.wav", mp3_dir / f"{part_index:02d}-{base_name}.mp3"


def write_mp3_from_wav(wav_path: Path, mp3_path: Path, bitrate_kbps: int, force: bool) -> Path:
    if not wav_path.exists():
        raise FileNotFoundError(f"Missing WAV input: {wav_path}")
    if mp3_path.exists() and not force:
        raise FileExistsError(f"Output already exists: {mp3_path}. Use --force to overwrite.")

    audio, sample_rate = sf.read(str(wav_path), dtype="float32", always_2d=False)
    if audio.ndim == 1:
        channels = 1
        pcm = np.clip(audio, -1.0, 1.0)
        interleaved = (pcm * 32767.0).astype(np.int16)
    else:
        channels = audio.shape[1]
        pcm = np.clip(audio, -1.0, 1.0)
        interleaved = (pcm * 32767.0).astype(np.int16).reshape(-1)

    encoder = lameenc.Encoder()
    encoder.set_bit_rate(bitrate_kbps)
    encoder.set_in_sample_rate(sample_rate)
    encoder.set_channels(channels)
    encoder.set_quality(2)

    mp3_bytes = encoder.encode(interleaved.tobytes())
    mp3_bytes += encoder.flush()
    mp3_path.parent.mkdir(parents=True, exist_ok=True)
    mp3_path.write_bytes(mp3_bytes)
    return mp3_path


def write_mp3_from_audio(audio: np.ndarray, sample_rate: int, mp3_path: Path, bitrate_kbps: int, force: bool) -> Path:
    if mp3_path.exists() and not force:
        raise FileExistsError(f"Output already exists: {mp3_path}. Use --force to overwrite.")

    if audio.ndim == 1:
        channels = 1
        pcm = np.clip(audio, -1.0, 1.0)
        interleaved = (pcm * 32767.0).astype(np.int16)
    else:
        channels = audio.shape[1]
        pcm = np.clip(audio, -1.0, 1.0)
        interleaved = (pcm * 32767.0).astype(np.int16).reshape(-1)

    encoder = lameenc.Encoder()
    encoder.set_bit_rate(bitrate_kbps)
    encoder.set_in_sample_rate(sample_rate)
    encoder.set_channels(channels)
    encoder.set_quality(2)

    mp3_bytes = encoder.encode(interleaved.tobytes())
    mp3_bytes += encoder.flush()
    mp3_path.parent.mkdir(parents=True, exist_ok=True)
    mp3_path.write_bytes(mp3_bytes)
    return mp3_path


def write_mp3_tags(mp3_path: Path, title: str, track_number: int, metadata: AudioMetadata, album_title: str | None = None) -> None:
    try:
        tags = EasyID3(str(mp3_path))
    except ID3NoHeaderError:
        tags = EasyID3()
    tags["title"] = [title]
    tags["tracknumber"] = [str(track_number)]
    tags["album"] = [album_title or metadata.source_title]
    if metadata.author:
        tags["artist"] = [metadata.author]
        tags["albumartist"] = [metadata.author]
    if metadata.published_date:
        tags["date"] = [metadata.published_date]
    tags.save(str(mp3_path))

    id3 = ID3(str(mp3_path))
    if metadata.source_title:
        id3.delall("COMM")
        id3.add(COMM(encoding=3, lang="eng", desc="source", text=metadata.source_title))
    if metadata.publisher:
        id3.delall("TPUB")
        id3.add(TPUB(encoding=3, text=metadata.publisher))
    id3.save(v2_version=3)


@contextlib.contextmanager
def cross_process_io_gate(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def extract_track_number(stem: str, fallback: int) -> int:
    match = re.match(r"^(\d+)", stem)
    if match:
        return int(match.group(1))
    return fallback


def build_output_paths(output_root: Path, part_count: int) -> list[tuple[Path, Path]]:
    if part_count <= 1:
        return [(output_root.with_suffix(".wav"), output_root.with_suffix(".mp3"))]
    return [
        (
            output_root.parent / f"{output_root.name}-part{part_index:02d}.wav",
            output_root.parent / f"{output_root.name}-part{part_index:02d}.mp3",
        )
        for part_index in range(1, part_count + 1)
    ]

