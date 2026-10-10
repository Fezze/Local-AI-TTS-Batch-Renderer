import shutil
import uuid
from pathlib import Path

import numpy as np
import pytest

from local_tts_renderer import cli, cli_render_flow
from test_cli_partial_resume import (
    SAMPLE_RATE, SAMPLES_PER_CHUNK, _resume_chapters, _expected_chunk_count, _render_kwargs,
)


@pytest.mark.parametrize("fresh", [False, True])
@pytest.mark.parametrize("force", [False, True])
def test_completed_render_flag_matrix(
    monkeypatch: pytest.MonkeyPatch,
    fresh: bool,
    force: bool,
) -> None:
    rendered_texts: list[str] = []

    def fake_create_audio_with_retry(**kwargs):  # type: ignore[no-untyped-def]
        rendered_texts.append(kwargs["text"])
        return [np.zeros(SAMPLES_PER_CHUNK, dtype=np.float32)], SAMPLE_RATE

    monkeypatch.setattr(cli_render_flow, "CREATE_AUDIO_WITH_RETRY", fake_create_audio_with_retry)
    chapters = _resume_chapters()
    expected_chunks = _expected_chunk_count(chapters)
    tmp_path = Path.cwd() / ".test_tmp" / f"tts-cli-completed-flags-{uuid.uuid4().hex}"
    tmp_path.mkdir(parents=True, exist_ok=True)
    try:
        kwargs = _render_kwargs(tmp_path, chapters)
        kwargs["max_part_minutes"] = 10.0
        initial = cli.render_audio(**kwargs)
        manifest_path = (kwargs["output_root"] / "04-Section Alpha").with_suffix(".json")
        initial_manifest_bytes = manifest_path.read_bytes()
        initial_mp3 = Path(initial["parts"][0]["mp3_path"])
        initial_mp3_bytes = initial_mp3.read_bytes()
        rendered_texts.clear()

        if force:
            rerendered = cli.render_audio(**{**kwargs, "fresh": fresh, "force": True})
            assert len(rendered_texts) == expected_chunks
            assert rerendered["chunk_count"] == expected_chunks
            assert initial_mp3.is_file()
        else:
            with pytest.raises(FileExistsError, match="Use --force"):
                cli.render_audio(**{**kwargs, "fresh": fresh})
            assert rendered_texts == []
            assert manifest_path.read_bytes() == initial_manifest_bytes
            assert initial_mp3.read_bytes() == initial_mp3_bytes
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)
