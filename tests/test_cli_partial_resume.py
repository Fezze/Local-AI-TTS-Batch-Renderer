from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import numpy as np
import pytest

from local_tts_renderer import cli
from local_tts_renderer import cli_chunking_utils
from local_tts_renderer import cli_render_flow
from local_tts_renderer.cli_resume import ResumeCheckpointError


SAMPLE_RATE = 24000
SAMPLES_PER_CHUNK = 2400
MAX_CHARS = 45


def _resume_chapters() -> list[cli.Chapter]:
    return [
        cli.Chapter(
            title="Section Alpha",
            text=(
                "First sentence has words. Second sentence has words. "
                "Third sentence has words. Fourth sentence has words."
            ),
            group=None,
        )
    ]


def _render_kwargs(tmp_path: Path, chapters: list[cli.Chapter]) -> dict:
    output_root = tmp_path / "doc"
    return {
        "kokoro": object(),
        "chapters": chapters,
        "base_output_dir": tmp_path,
        "output_root": output_root,
        "group_dir_map": {},
        "voice": "voice_a",
        "lang": "en-us",
        "trim_mode": "off",
        "speed": 1.0,
        "max_chars": MAX_CHARS,
        "silence_ms": 0,
        "max_part_minutes": 0.0005,
        "keep_chunks": False,
        "mp3_only": True,
        "force": False,
        "audio_metadata": None,
        "heartbeat_seconds": 0.0,
        "final_stem_override": "04-Section Alpha",
        "max_parts_per_run": 0,
    }


def _expected_chunk_count(chapters: list[cli.Chapter]) -> int:
    return len(
        cli_chunking_utils.chunk_section(
            chapters[0].title,
            chapters[0].text,
            max_chars=MAX_CHARS,
            start_index=1,
        )
    )


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


def test_render_audio_resumes_cleanly_after_failure_before_first_close(monkeypatch: pytest.MonkeyPatch) -> None:
    rendered_texts: list[str] = []

    def fake_create_audio_with_retry(**kwargs):  # type: ignore[no-untyped-def]
        rendered_texts.append(kwargs["text"])
        return [np.zeros(SAMPLES_PER_CHUNK, dtype=np.float32)], SAMPLE_RATE

    original_write_audio = cli_render_flow.OutputPartWriter.write_audio
    fail_once = True

    def write_audio_then_fail_once(self, audio):  # type: ignore[no-untyped-def]
        nonlocal fail_once
        original_write_audio(self, audio)
        if fail_once:
            fail_once = False
            raise RuntimeError("injected failure before first close")

    monkeypatch.setattr(cli_render_flow, "CREATE_AUDIO_WITH_RETRY", fake_create_audio_with_retry)
    monkeypatch.setattr(cli_render_flow.OutputPartWriter, "write_audio", write_audio_then_fail_once)

    chapters = _resume_chapters()
    expected_chunks = _expected_chunk_count(chapters)
    assert expected_chunks > 1
    tmp_path = Path.cwd() / ".test_tmp" / f"tts-cli-resume-before-close-{uuid.uuid4().hex}"
    tmp_path.mkdir(parents=True, exist_ok=True)
    try:
        kwargs = _render_kwargs(tmp_path, chapters)
        kwargs["max_part_minutes"] = 10.0
        checkpoint_path = (kwargs["output_root"] / "04-Section Alpha").with_suffix(".resume.json")

        with pytest.raises(RuntimeError, match="injected failure before first close"):
            cli.render_audio(**kwargs)

        state = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        assert state["completed_chunks"] == 0
        assert state["next_chunk_index"] == 1
        assert state["output_parts"] == []
        assert state["manifest_chunks"] == []

        manifest = cli.render_audio(**kwargs)

        chunk_indices = [chunk["index"] for chunk in manifest["chunks"]]
        assert chunk_indices == list(range(1, expected_chunks + 1))
        assert len(rendered_texts) == expected_chunks + 1
        assert rendered_texts[0] == rendered_texts[1]
        assert manifest["parts"][0]["duration_seconds"] == pytest.approx(
            expected_chunks * SAMPLES_PER_CHUNK / SAMPLE_RATE
        )
        assert not checkpoint_path.exists()
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_default_render_preserves_orphaned_final_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli_render_flow,
        "CREATE_AUDIO_WITH_RETRY",
        lambda **kwargs: pytest.fail("render must not start when output collides"),
    )
    chapters = _resume_chapters()
    tmp_path = Path.cwd() / ".test_tmp" / f"tts-cli-orphan-output-{uuid.uuid4().hex}"
    tmp_path.mkdir(parents=True, exist_ok=True)
    try:
        kwargs = _render_kwargs(tmp_path, chapters)
        orphan = tmp_path / "mp3" / "doc" / "04-02 - Section Alpha.mp3"
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_bytes(b"preexisting-audio")
        checkpoint_path = (kwargs["output_root"] / "04-Section Alpha").with_suffix(".resume.json")

        with pytest.raises(FileExistsError, match="Unfinished output"):
            cli.render_audio(**kwargs)

        assert orphan.read_bytes() == b"preexisting-audio"
        assert not checkpoint_path.exists()
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_resume_removes_part_closed_before_checkpoint_save(monkeypatch: pytest.MonkeyPatch) -> None:
    rendered_texts: list[str] = []

    def fake_create_audio_with_retry(**kwargs):  # type: ignore[no-untyped-def]
        rendered_texts.append(kwargs["text"])
        return [np.zeros(SAMPLES_PER_CHUNK, dtype=np.float32)], SAMPLE_RATE

    original_save_checkpoint = cli_render_flow.save_safe_checkpoint
    fail_after_close = True

    def save_checkpoint_then_fail(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal fail_after_close
        if fail_after_close and kwargs.get("completed_chunks", 0) > 0:
            fail_after_close = False
            raise RuntimeError("injected failure after part close")
        return original_save_checkpoint(*args, **kwargs)

    monkeypatch.setattr(cli_render_flow, "CREATE_AUDIO_WITH_RETRY", fake_create_audio_with_retry)
    monkeypatch.setattr(cli_render_flow, "save_safe_checkpoint", save_checkpoint_then_fail)

    chapters = _resume_chapters()
    expected_chunks = _expected_chunk_count(chapters)
    tmp_path = Path.cwd() / ".test_tmp" / f"tts-cli-close-before-save-{uuid.uuid4().hex}"
    tmp_path.mkdir(parents=True, exist_ok=True)
    try:
        kwargs = _render_kwargs(tmp_path, chapters)
        checkpoint_path = (kwargs["output_root"] / "04-Section Alpha").with_suffix(".resume.json")
        uncommitted_part = tmp_path / "mp3" / "doc" / "04-01 - Section Alpha.mp3"

        with pytest.raises(RuntimeError, match="injected failure after part close"):
            cli.render_audio(**kwargs)

        state = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        assert state["completed_chunks"] == 0
        assert uncommitted_part.is_file()

        monkeypatch.setattr(cli_render_flow, "save_safe_checkpoint", original_save_checkpoint)
        manifest = cli.render_audio(**kwargs)

        assert len(rendered_texts) == expected_chunks + 1
        assert [chunk["index"] for chunk in manifest["chunks"]] == list(
            range(1, expected_chunks + 1)
        )
        assert not checkpoint_path.exists()
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_force_resumes_after_closed_part_without_rendering_committed_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    rendered_texts: list[str] = []

    def fake_create_audio_with_retry(**kwargs):  # type: ignore[no-untyped-def]
        rendered_texts.append(kwargs["text"])
        return [np.zeros(SAMPLES_PER_CHUNK, dtype=np.float32)], SAMPLE_RATE

    monkeypatch.setattr(cli_render_flow, "CREATE_AUDIO_WITH_RETRY", fake_create_audio_with_retry)

    chapters = _resume_chapters()
    expected_chunks = _expected_chunk_count(chapters)
    assert expected_chunks > 1
    tmp_path = Path.cwd() / ".test_tmp" / f"tts-cli-force-resume-{uuid.uuid4().hex}"
    tmp_path.mkdir(parents=True, exist_ok=True)
    try:
        kwargs = _render_kwargs(tmp_path, chapters)
        checkpoint_path = (kwargs["output_root"] / "04-Section Alpha").with_suffix(".resume.json")

        with pytest.raises(cli.PartialRunComplete):
            cli.render_audio(**{**kwargs, "max_parts_per_run": 1})

        state = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        assert state["completed_chunks"] == 1
        assert len(rendered_texts) == 1

        manifest = cli.render_audio(**{**kwargs, "force": True})

        assert len(rendered_texts) == expected_chunks
        assert [chunk["index"] for chunk in manifest["chunks"]] == list(range(1, expected_chunks + 1))
        assert [part["start_chunk"] for part in manifest["parts"]] == list(range(1, expected_chunks + 1))
        assert all(Path(part["mp3_path"]).is_file() for part in manifest["parts"])
        assert not checkpoint_path.exists()
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


@pytest.mark.parametrize("manifest_kind", ["corrupt", "partial"])
def test_checkpoint_takes_precedence_over_incomplete_manifest(
    monkeypatch: pytest.MonkeyPatch,
    manifest_kind: str,
) -> None:
    rendered_texts: list[str] = []

    def fake_create_audio_with_retry(**kwargs):  # type: ignore[no-untyped-def]
        rendered_texts.append(kwargs["text"])
        return [np.zeros(SAMPLES_PER_CHUNK, dtype=np.float32)], SAMPLE_RATE

    monkeypatch.setattr(cli_render_flow, "CREATE_AUDIO_WITH_RETRY", fake_create_audio_with_retry)
    chapters = _resume_chapters()
    expected_chunks = _expected_chunk_count(chapters)
    tmp_path = Path.cwd() / ".test_tmp" / f"tts-cli-manifest-checkpoint-{uuid.uuid4().hex}"
    tmp_path.mkdir(parents=True, exist_ok=True)
    try:
        kwargs = _render_kwargs(tmp_path, chapters)
        checkpoint_path = (kwargs["output_root"] / "04-Section Alpha").with_suffix(".resume.json")
        manifest_path = (kwargs["output_root"] / "04-Section Alpha").with_suffix(".json")

        with pytest.raises(cli.PartialRunComplete):
            cli.render_audio(**{**kwargs, "max_parts_per_run": 1})
        assert checkpoint_path.exists()
        state = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if manifest_kind == "corrupt":
            manifest_path.write_text("{", encoding="utf-8")
        else:
            manifest_path.write_text(
                json.dumps(
                    {
                        "chapter_count": 1,
                        "chunk_count": len(state["manifest_chunks"]),
                        "parts": state["output_parts"],
                        "chunks": state["manifest_chunks"],
                    }
                ),
                encoding="utf-8",
            )

        manifest = cli.render_audio(**kwargs)

        assert len(rendered_texts) == expected_chunks
        assert manifest["chunk_count"] == expected_chunks
        assert json.loads(manifest_path.read_text(encoding="utf-8"))["chunk_count"] == expected_chunks
        assert not checkpoint_path.exists()
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_complete_manifest_takes_precedence_over_stale_checkpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    rendered_texts: list[str] = []

    def fake_create_audio_with_retry(**kwargs):  # type: ignore[no-untyped-def]
        rendered_texts.append(kwargs["text"])
        return [np.zeros(SAMPLES_PER_CHUNK, dtype=np.float32)], SAMPLE_RATE

    monkeypatch.setattr(cli_render_flow, "CREATE_AUDIO_WITH_RETRY", fake_create_audio_with_retry)
    chapters = _resume_chapters()
    expected_chunks = _expected_chunk_count(chapters)
    tmp_path = Path.cwd() / ".test_tmp" / f"tts-cli-complete-stale-checkpoint-{uuid.uuid4().hex}"
    tmp_path.mkdir(parents=True, exist_ok=True)
    try:
        kwargs = _render_kwargs(tmp_path, chapters)
        kwargs["max_part_minutes"] = 10.0
        checkpoint_path = (kwargs["output_root"] / "04-Section Alpha").with_suffix(".resume.json")
        manifest_path = (kwargs["output_root"] / "04-Section Alpha").with_suffix(".json")
        original_unlink = Path.unlink
        fail_once = True

        def fail_checkpoint_unlink_once(path: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal fail_once
            if fail_once and path == checkpoint_path:
                fail_once = False
                raise OSError("injected failure after manifest publication")
            return original_unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", fail_checkpoint_unlink_once)
        with pytest.raises(OSError, match="after manifest publication"):
            cli.render_audio(**kwargs)

        assert manifest_path.is_file()
        assert checkpoint_path.is_file()
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
        mp3_path = Path(manifest["parts"][0]["mp3_path"])
        mp3_bytes = mp3_path.read_bytes()
        rendered_texts.clear()
        monkeypatch.setattr(Path, "unlink", original_unlink)

        with pytest.raises(FileExistsError, match="Use --force"):
            cli.render_audio(**kwargs)

        assert rendered_texts == []
        assert checkpoint_path.is_file()
        assert manifest_path.read_bytes() == manifest_bytes
        assert mp3_path.read_bytes() == mp3_bytes

        rerendered = cli.render_audio(**{**kwargs, "force": True})

        assert rerendered["chunk_count"] == expected_chunks
        assert len(rendered_texts) == expected_chunks
        assert not checkpoint_path.exists()
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_resume_rejects_checkpoint_inconsistent_with_chunk_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_create_audio_with_retry(**kwargs):  # type: ignore[no-untyped-def]
        return [np.zeros(SAMPLES_PER_CHUNK, dtype=np.float32)], SAMPLE_RATE

    monkeypatch.setattr(cli_render_flow, "CREATE_AUDIO_WITH_RETRY", fake_create_audio_with_retry)
    chapters = _resume_chapters()
    tmp_path = Path.cwd() / ".test_tmp" / f"tts-cli-invalid-plan-{uuid.uuid4().hex}"
    tmp_path.mkdir(parents=True, exist_ok=True)
    try:
        kwargs = _render_kwargs(tmp_path, chapters)
        checkpoint_path = (kwargs["output_root"] / "04-Section Alpha").with_suffix(".resume.json")

        with pytest.raises(cli.PartialRunComplete):
            cli.render_audio(**{**kwargs, "max_parts_per_run": 1})
        state = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        committed_path = Path(state["output_parts"][0]["mp3_path"])
        committed_bytes = committed_path.read_bytes()
        state["next_chapter_index"] = 2
        checkpoint_path.write_text(json.dumps(state), encoding="utf-8")

        with pytest.raises(ResumeCheckpointError, match="next chapter"):
            cli.render_audio(**kwargs)

        assert committed_path.read_bytes() == committed_bytes
        assert checkpoint_path.exists()
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


@pytest.mark.parametrize("fresh_force", [False, True])
def test_force_rejects_resume_mismatch_and_fresh_restarts_safely(
    monkeypatch: pytest.MonkeyPatch,
    fresh_force: bool,
) -> None:
    rendered_texts: list[str] = []

    def fake_create_audio_with_retry(**kwargs):  # type: ignore[no-untyped-def]
        rendered_texts.append(kwargs["text"])
        return [np.zeros(SAMPLES_PER_CHUNK, dtype=np.float32)], SAMPLE_RATE

    monkeypatch.setattr(cli_render_flow, "CREATE_AUDIO_WITH_RETRY", fake_create_audio_with_retry)

    chapters = _resume_chapters()
    expected_chunks = _expected_chunk_count(chapters)
    assert expected_chunks > 1
    tmp_path = Path.cwd() / ".test_tmp" / f"tts-cli-fresh-restart-{uuid.uuid4().hex}"
    tmp_path.mkdir(parents=True, exist_ok=True)
    try:
        kwargs = _render_kwargs(tmp_path, chapters)
        checkpoint_path = (kwargs["output_root"] / "04-Section Alpha").with_suffix(".resume.json")

        with pytest.raises(cli.PartialRunComplete):
            cli.render_audio(**{**kwargs, "max_parts_per_run": 1})
        assert len(rendered_texts) == 1

        with pytest.raises(ResumeCheckpointError, match="configuration has changed") as error:
            cli.render_audio(**{**kwargs, "speed": 1.1, "force": True})
        assert "--fresh" in str(error.value)
        assert len(rendered_texts) == 1
        assert checkpoint_path.exists()

        manifest = cli.render_audio(
            **{**kwargs, "speed": 1.1, "fresh": True, "force": fresh_force}
        )

        assert manifest["speed"] == 1.1
        assert len(rendered_texts) == expected_chunks + 1
        assert [chunk["index"] for chunk in manifest["chunks"]] == list(range(1, expected_chunks + 1))
        assert len(manifest["parts"]) == expected_chunks
        assert all(Path(part["mp3_path"]).is_file() for part in manifest["parts"])
        assert not checkpoint_path.exists()
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


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
