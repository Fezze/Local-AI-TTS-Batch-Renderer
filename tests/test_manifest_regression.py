from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import numpy as np

from local_tts_renderer import cli_core, cli_render_flow
from local_tts_renderer.cli_models import AudioMetadata
from local_tts_renderer.input_parsers import Chapter


def _mk_tmp_dir() -> Path:
    path = Path.cwd() / ".test_tmp" / f"manifest-regression-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_render_audio_manifest_preserves_chunk_order(monkeypatch) -> None:
    def fake_create_audio_with_retry(**kwargs):  # type: ignore[no-untyped-def]
        return [np.zeros(24000, dtype=np.float32)], 24000

    monkeypatch.setattr(cli_render_flow, "CREATE_AUDIO_WITH_RETRY", fake_create_audio_with_retry)

    chapters = [
        Chapter(title="Alpha", text="One two three. Four five six.", group=None),
        Chapter(title="Beta", text="Seven eight nine. Ten eleven twelve.", group=None),
    ]
    tmp_path = _mk_tmp_dir()
    try:
        output_root = tmp_path / "doc"
        manifest = cli_core.render_audio(
            kokoro=object(),
            chapters=chapters,
            base_output_dir=tmp_path,
            output_root=output_root,
            group_dir_map={},
            voice="voice_a",
            lang="en-us",
            trim_mode="off",
            speed=1.0,
            max_chars=40,
            silence_ms=0,
            max_part_minutes=10.0,
            keep_chunks=False,
            mp3_only=True,
            force=True,
            audio_metadata=AudioMetadata(source_title="Test Source"),
            heartbeat_seconds=0.0,
            final_stem_override="01-Test Source",
            max_parts_per_run=0,
        )

        manifest_path = output_root / "01-Test Source.json"
        saved = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert [chunk["chapter"] for chunk in manifest["chunks"]] == ["Alpha", "Beta"]
        assert [chunk["chapter"] for chunk in saved["chunks"]] == ["Alpha", "Beta"]
        assert [chunk["index"] for chunk in saved["chunks"]] == sorted(chunk["index"] for chunk in saved["chunks"])
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)
