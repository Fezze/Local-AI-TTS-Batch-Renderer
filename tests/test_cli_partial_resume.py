from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import numpy as np
import pytest

from local_tts_renderer import cli
from local_tts_renderer import cli_render_flow


def test_render_audio_partial_run_writes_resume_checkpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_create_audio_with_retry(**kwargs):  # type: ignore[no-untyped-def]
        return [np.zeros(24000, dtype=np.float32)], 24000

    monkeypatch.setattr(cli_render_flow, "CREATE_AUDIO_WITH_RETRY", fake_create_audio_with_retry)

    chapters = [cli.Chapter(title="Section Alpha", text="Sentence. " * 220, group=None)]
    tmp_path = Path.cwd() / ".test_tmp" / f"tts-cli-partial-{uuid.uuid4().hex}"
    tmp_path.mkdir(parents=True, exist_ok=True)
    try:
        output_root = tmp_path / "doc"
        checkpoint_path = (output_root / "04-Section Alpha").with_suffix(".resume.json")

        with pytest.raises(cli.PartialRunComplete):
            cli.render_audio(
                kokoro=object(),
                chapters=chapters,
                base_output_dir=tmp_path,
                output_root=output_root,
                group_dir_map={},
                voice="voice_a",
                lang="en-us",
                trim_mode="off",
                speed=1.0,
                max_chars=80,
                silence_ms=0,
                max_part_minutes=0.0005,
                keep_chunks=False,
                mp3_only=True,
                force=False,
                audio_metadata=cli.AudioMetadata(source_title="Test Source"),
                heartbeat_seconds=0.0,
                final_stem_override="04-Section Alpha",
                max_parts_per_run=1,
            )

        assert checkpoint_path.exists()
        state = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        assert state["next_part_index"] == 2
        assert state["next_chunk_index"] > 1
        assert state["next_chapter_index"] == 1
        first_part_path = tmp_path / "mp3" / "doc" / "04-01 - Section Alpha.mp3"
        assert first_part_path.exists()
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)
