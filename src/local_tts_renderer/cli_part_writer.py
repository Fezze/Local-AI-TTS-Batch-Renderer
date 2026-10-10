from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf

from .cli_audio_utils import (
    build_mp3_encoder,
    build_temp_part_base_name,
    compute_part_output_paths,
    cross_process_io_gate,
    extract_track_number,
    safe_remove_path,
    write_mp3_tags,
)
from .cli_models import AudioMetadata
from .document_helpers import get_group_leaf_title, sanitize_filename_component


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
        self.force = force
        self.final_stem_override = final_stem_override
        self.base_name = build_temp_part_base_name(part_index=part_index, final_stem_override=final_stem_override)
        self.wav_path, self.mp3_path = compute_part_output_paths(
            output_root,
            base_output_dir,
            part_index,
            multi_part,
            self.base_name,
            group_name,
            final_stem_override,
        )

        if not force and (self.wav_path.exists() or self.mp3_path.exists()):
            raise FileExistsError(f"Output already exists for {self.wav_path.stem}. Use --force to overwrite.")

        if not self.mp3_only:
            self.wav_path.parent.mkdir(parents=True, exist_ok=True)
        self.mp3_path.parent.mkdir(parents=True, exist_ok=True)
        self.wav_file = None if self.mp3_only else sf.SoundFile(
            str(self.wav_path),
            mode="w",
            samplerate=sample_rate,
            channels=1,
            subtype="PCM_16",
        )
        self.encoder = build_mp3_encoder(sample_rate=sample_rate, bitrate_kbps=192, channels=1)
        self.mp3_handle = self.mp3_path.open("wb")
        self.closed = False
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
        self.closed = True
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
            moves: list[tuple[Path, Path]] = []
            if not self.mp3_only and self.wav_path != final_wav_path:
                moves.append((self.wav_path, final_wav_path))
            if self.mp3_path != final_mp3_path:
                moves.append((self.mp3_path, final_mp3_path))
            collisions = [destination for _, destination in moves if destination.exists()]
            if collisions and not self.force:
                raise FileExistsError(
                    f"Output already exists: {collisions[0]}. Use --force to overwrite."
                )
            for source, destination in moves:
                if destination.exists():
                    if not safe_remove_path(destination) and destination.exists():
                        raise OSError(f"Could not replace existing output: {destination}")
                source.replace(destination)
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

    def abort(self) -> None:
        if self.closed:
            return
        if not self.mp3_handle.closed:
            self.mp3_handle.close()
        if self.wav_file is not None:
            self.wav_file.close()
        self.closed = True


__all__ = ["OutputPartWriter"]
