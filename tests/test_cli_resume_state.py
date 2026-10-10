from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_tts_renderer import atomic_io, cli_render_cleanup, cli_resume
from local_tts_renderer.cli_models import AudioMetadata
from local_tts_renderer.sources.model import SourceChapter


def _fingerprint(**overrides: object) -> str:
    values: dict[str, object] = {
        "chapters": [SourceChapter(title="One", text="Neutral text.", group=None)],
        "voice": "voice_a",
        "lang": "en-us",
        "trim_mode": "off",
        "speed": 1.0,
        "max_chars": 900,
        "max_phoneme_chars": 0,
        "silence_ms": 250,
        "max_part_minutes": 30.0,
        "keep_chunks": False,
        "mp3_only": True,
        "audio_metadata": AudioMetadata(source_title="Neutral Source"),
        "group_dir_map": {"Book / Part": Path("01-Book/01-Part")},
        "final_stem_override": "01-One",
    }
    values.update(overrides)
    return cli_resume.build_resume_fingerprint(**values)  # type: ignore[arg-type]


def _empty_state() -> dict:
    return {
        "next_chapter_index": 1,
        "next_chunk_index": 1,
        "completed_chunks": 0,
        "elapsed_seconds": 0.0,
        "sample_rate": None,
        "output_parts": [],
        "manifest_chunks": [],
        "next_group": None,
        "next_part_index": 1,
        "render_max_chars": 900,
    }


def _progressed_state(mp3_path: Path) -> dict:
    return {
        "next_chapter_index": 1,
        "next_chunk_index": 2,
        "completed_chunks": 1,
        "elapsed_seconds": 2.5,
        "sample_rate": 24000,
        "output_parts": [
            {
                "part": 1,
                "wav_path": None,
                "mp3_path": str(mp3_path),
                "start_chunk": 1,
                "end_chunk": 1,
                "group": None,
            }
        ],
        "manifest_chunks": [{"index": 1, "text": "Neutral text."}],
        "next_group": None,
        "next_part_index": 2,
        "render_max_chars": 900,
    }


def test_fingerprint_is_canonical_and_sensitive_to_render_input() -> None:
    left = _fingerprint(group_dir_map={"B": Path("02-B"), "A": Path("01-A")})
    right = _fingerprint(group_dir_map={"A": "01-A", "B": "02-B"})

    assert left == right
    assert left != _fingerprint(chapters=[SourceChapter(title="One", text="Changed text.")])
    assert left != _fingerprint(group_dir_map={"A": "01-A", "B": "02-B"}, speed=1.1)
    assert left != _fingerprint(model_identity={"model": {"size": 123}})
    assert len(left) == 64


def test_canonical_json_rejects_non_finite_values() -> None:
    with pytest.raises(ValueError):
        cli_resume.canonical_json_bytes({"speed": float("nan")})


def test_atomic_save_fsyncs_and_replaces_then_loads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    checkpoint = tmp_path / "job.resume.json"
    fingerprint = _fingerprint()
    replace_calls: list[tuple[Path, Path]] = []
    fsync_calls: list[int] = []
    real_replace = atomic_io.os.replace
    real_fsync = atomic_io.os.fsync

    def tracking_replace(source: Path, target: Path) -> None:
        replace_calls.append((Path(source), Path(target)))
        real_replace(source, target)

    def tracking_fsync(descriptor: int) -> None:
        fsync_calls.append(descriptor)
        real_fsync(descriptor)

    monkeypatch.setattr(atomic_io.os, "replace", tracking_replace)
    monkeypatch.setattr(atomic_io.os, "fsync", tracking_fsync)

    saved = cli_resume.save_resume_state(checkpoint, _empty_state(), fingerprint=fingerprint)
    loaded = cli_resume.load_resume_state(
        checkpoint,
        expected_fingerprint=fingerprint,
        expected_render_max_chars=900,
    )

    assert saved["schema_version"] == cli_resume.RESUME_SCHEMA_VERSION
    assert loaded == saved
    assert replace_calls and replace_calls[0][1] == checkpoint
    assert fsync_calls
    assert not list(tmp_path.glob(".job.resume.json.*.tmp"))


def test_failed_atomic_replace_preserves_previous_checkpoint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    checkpoint = tmp_path / "job.resume.json"
    fingerprint = _fingerprint()
    cli_resume.save_resume_state(checkpoint, _empty_state(), fingerprint=fingerprint)
    original = checkpoint.read_bytes()
    changed = _empty_state()
    changed["elapsed_seconds"] = 4.0

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(atomic_io.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        cli_resume.save_resume_state(checkpoint, changed, fingerprint=fingerprint)

    assert checkpoint.read_bytes() == original
    assert not list(tmp_path.glob(".job.resume.json.*.tmp"))


@pytest.mark.parametrize("content", ["{", "[]", '"text"'])
def test_corrupt_or_non_object_checkpoint_has_actionable_error(tmp_path: Path, content: str) -> None:
    checkpoint = tmp_path / "job.resume.json"
    checkpoint.write_text(content, encoding="utf-8")

    with pytest.raises(cli_resume.ResumeCheckpointError) as error:
        cli_resume.load_resume_state(
            checkpoint,
            expected_fingerprint=_fingerprint(),
            expected_render_max_chars=900,
        )

    assert str(checkpoint) in str(error.value)
    assert "--fresh" in str(error.value)


def test_fingerprint_mismatch_is_rejected_without_mutation(tmp_path: Path) -> None:
    checkpoint = tmp_path / "job.resume.json"
    cli_resume.save_resume_state(checkpoint, _empty_state(), fingerprint=_fingerprint())
    before = checkpoint.read_bytes()

    with pytest.raises(cli_resume.ResumeCheckpointError, match="configuration has changed"):
        cli_resume.load_resume_state(
            checkpoint,
            expected_fingerprint=_fingerprint(voice="voice_b"),
            expected_render_max_chars=900,
        )

    assert checkpoint.read_bytes() == before


def test_pinned_chunk_size_mismatch_is_rejected(tmp_path: Path) -> None:
    checkpoint = tmp_path / "job.resume.json"
    fingerprint = _fingerprint()
    cli_resume.save_resume_state(checkpoint, _empty_state(), fingerprint=fingerprint)

    with pytest.raises(cli_resume.ResumeCheckpointError, match="pinned chunk size"):
        cli_resume.load_resume_state(
            checkpoint,
            expected_fingerprint=fingerprint,
            expected_render_max_chars=800,
        )


def test_empty_legacy_checkpoint_is_upgraded_atomically(tmp_path: Path) -> None:
    checkpoint = tmp_path / "job.resume.json"
    checkpoint.write_text(json.dumps({"elapsed_seconds": 1.25, "next_group": "Part"}), encoding="utf-8")
    fingerprint = _fingerprint()

    state = cli_resume.load_resume_state(
        checkpoint,
        expected_fingerprint=fingerprint,
        expected_render_max_chars=900,
    )
    persisted = json.loads(checkpoint.read_text(encoding="utf-8"))

    assert state is not None
    assert state["schema_version"] == 2
    assert state["fingerprint"] == fingerprint
    assert state["elapsed_seconds"] == 1.25
    assert state["render_max_chars"] == 900
    assert persisted == state


def test_progressed_legacy_checkpoint_requires_fresh(tmp_path: Path) -> None:
    checkpoint = tmp_path / "job.resume.json"
    legacy = _progressed_state(tmp_path / "part.mp3")
    checkpoint.write_text(json.dumps(legacy), encoding="utf-8")

    with pytest.raises(cli_resume.ResumeCheckpointError) as error:
        cli_resume.load_resume_state(
            checkpoint,
            expected_fingerprint=_fingerprint(),
            expected_render_max_chars=900,
        )

    assert "legacy" in str(error.value)
    assert "--fresh" in str(error.value)


def test_loading_can_validate_committed_artifacts(tmp_path: Path) -> None:
    checkpoint = tmp_path / "job.resume.json"
    missing_mp3 = tmp_path / "missing.mp3"
    fingerprint = _fingerprint()
    cli_resume.save_resume_state(checkpoint, _progressed_state(missing_mp3), fingerprint=fingerprint)

    with pytest.raises(cli_resume.ResumeCheckpointError, match="missing or empty"):
        cli_resume.load_resume_state(
            checkpoint,
            expected_fingerprint=fingerprint,
            expected_render_max_chars=900,
        )

    missing_mp3.write_bytes(b"audio")
    assert cli_resume.load_resume_state(
        checkpoint,
        expected_fingerprint=fingerprint,
        expected_render_max_chars=900,
    ) is not None


def test_inconsistent_progress_is_rejected(tmp_path: Path) -> None:
    checkpoint = tmp_path / "job.resume.json"
    fingerprint = _fingerprint()
    payload = {**_empty_state(), "schema_version": 2, "fingerprint": fingerprint, "next_chunk_index": 2}
    checkpoint.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(cli_resume.ResumeCheckpointError, match="next chunk index"):
        cli_resume.load_resume_state(
            checkpoint,
            expected_fingerprint=fingerprint,
            expected_render_max_chars=900,
        )


def test_inconsistent_next_part_is_rejected(tmp_path: Path) -> None:
    checkpoint = tmp_path / "job.resume.json"
    mp3_path = tmp_path / "part.mp3"
    mp3_path.write_bytes(b"audio")
    fingerprint = _fingerprint()
    payload = cli_resume.save_resume_state(
        checkpoint,
        _progressed_state(mp3_path),
        fingerprint=fingerprint,
    )
    payload["next_part_index"] = 1
    checkpoint.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(cli_resume.ResumeCheckpointError, match="next part index"):
        cli_resume.load_resume_state(
            checkpoint,
            expected_fingerprint=fingerprint,
            expected_render_max_chars=900,
        )


def test_duplicate_output_artifact_paths_are_rejected(tmp_path: Path) -> None:
    checkpoint = tmp_path / "job.resume.json"
    mp3_path = tmp_path / "part.mp3"
    mp3_path.write_bytes(b"audio")
    fingerprint = _fingerprint()
    state = _progressed_state(mp3_path)
    state.update(
        {
            "next_chunk_index": 3,
            "completed_chunks": 2,
            "manifest_chunks": [
                {"index": 1, "text": "First."},
                {"index": 2, "text": "Second."},
            ],
            "next_part_index": 3,
        }
    )
    state["output_parts"].append(
        {
            "part": 2,
            "wav_path": None,
            "mp3_path": mp3_path.name,
            "start_chunk": 2,
            "end_chunk": 2,
            "group": None,
        }
    )
    payload = {**state, "schema_version": 2, "fingerprint": fingerprint}
    checkpoint.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(cli_resume.ResumeCheckpointError, match="referenced more than once") as error:
        cli_resume.load_resume_state(
            checkpoint,
            expected_fingerprint=fingerprint,
            expected_render_max_chars=900,
        )

    assert "--fresh" in str(error.value)
    checkpoint.unlink()
    with pytest.raises(cli_resume.ResumeCheckpointError, match="referenced more than once"):
        cli_resume.save_resume_state(checkpoint, state, fingerprint=fingerprint)
    assert not checkpoint.exists()


def test_complete_manifest_rejects_duplicate_artifact_paths(tmp_path: Path) -> None:
    output_root = tmp_path / "book"
    manifest_path = output_root / "04-Section Alpha.json"
    mp3_path = tmp_path / "mp3" / "book" / "04-01 - Section Alpha.mp3"
    mp3_path.parent.mkdir(parents=True)
    mp3_path.write_bytes(b"audio")
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps(
            {
                "chapter_count": 1,
                "chunk_count": 2,
                "parts": [
                    {
                        "part": part,
                        "wav_path": None,
                        "mp3_path": str(mp3_path),
                        "start_chunk": part,
                        "end_chunk": part,
                        "group": None,
                    }
                    for part in (1, 2)
                ],
                "chunks": [
                    {
                        "index": index,
                        "heading": "Section Alpha",
                        "chapter": "Section Alpha",
                        "chars": len(text),
                        "text": text,
                    }
                    for index, text in enumerate(("First.", "Second."), start=1)
                ],
            }
        ),
        encoding="utf-8",
    )

    assert cli_render_cleanup.load_complete_manifest(
        manifest_path=manifest_path,
        base_output_dir=tmp_path,
        output_root=output_root,
        final_stem_override="04-Section Alpha",
        expected_chapters=[("Section Alpha", "First. Second.")],
    ) is None


def test_remove_exact_artifacts_deletes_only_explicit_job_paths(tmp_path: Path) -> None:
    output_root = tmp_path / "out"
    output_root.mkdir()
    checkpoint = output_root / "book" / "01-One.resume.json"
    committed_mp3 = output_root / "mp3" / "book" / "01-01 - One.mp3"
    committed_wav = output_root / "wav" / "book" / "01-01 - One.wav"
    pending_mp3 = output_root / "mp3" / "book" / "02-tmp-01-One-part-02.mp3"
    neighbor = output_root / "mp3" / "book" / "02-Two.mp3"
    chunk_dir = output_root / "book" / "01-One-chunks"
    for path in (checkpoint, committed_mp3, committed_wav, pending_mp3, neighbor, chunk_dir / "0001.wav"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    cli_resume.remove_exact_artifacts(
        allowed_root=output_root,
        files=[checkpoint, committed_mp3, committed_wav, pending_mp3],
        directories=[chunk_dir],
    )

    assert not checkpoint.exists()
    assert not committed_mp3.exists()
    assert not committed_wav.exists()
    assert not pending_mp3.exists()
    assert not chunk_dir.exists()
    assert neighbor.read_bytes() == b"x"


def test_removal_rejects_outside_path_before_deleting_any_file(tmp_path: Path) -> None:
    output_root = tmp_path / "out"
    output_root.mkdir()
    inside = output_root / "inside.mp3"
    outside = tmp_path / "outside.mp3"
    inside.write_bytes(b"inside")
    outside.write_bytes(b"outside")

    with pytest.raises(cli_resume.ResumeCheckpointError, match="outside"):
        cli_resume.remove_exact_artifacts(files=[inside, outside], allowed_root=output_root)

    assert inside.read_bytes() == b"inside"
    assert outside.read_bytes() == b"outside"


def test_render_reset_uses_job_identity_not_paths_from_json(tmp_path: Path) -> None:
    output_root = tmp_path / "book"
    manifest = output_root / "04-Section Alpha.json"
    checkpoint = output_root / "04-Section Alpha.resume.json"
    chunk_dir = output_root / "04-Section Alpha-chunks"
    owned_mp3 = tmp_path / "mp3" / "book" / "04-02 - Section Alpha.mp3"
    owned_wav = tmp_path / "wav" / "book" / "04-02 - Section Alpha.wav"
    neighbor = tmp_path / "mp3" / "book" / "05-Neighbor.mp3"
    injected = tmp_path / "mp3" / "other" / "keep-me.mp3"
    same_stem_text = tmp_path / "mp3" / "book" / "04-Section Alpha.txt"
    for path in (owned_mp3, owned_wav, neighbor, injected, same_stem_text, chunk_dir / "0001.wav"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"keep-or-remove")
    payload = {"parts": [{"part": 1, "mp3_path": str(injected), "wav_path": None}]}
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    checkpoint.write_text(json.dumps({"output_parts": payload["parts"]}), encoding="utf-8")

    cli_render_cleanup.reset_render_artifacts(
        base_output_dir=tmp_path,
        output_root=output_root,
        manifest_path=manifest,
        checkpoint_path=checkpoint,
        chunk_dir=chunk_dir,
        final_stem_override="04-Section Alpha",
        include_manifest=True,
    )

    assert not owned_mp3.exists()
    assert not owned_wav.exists()
    assert not manifest.exists()
    assert not checkpoint.exists()
    assert not chunk_dir.exists()
    assert neighbor.read_bytes() == b"keep-or-remove"
    assert injected.read_bytes() == b"keep-or-remove"
    assert same_stem_text.read_bytes() == b"keep-or-remove"


def test_resume_cleanup_rejects_committed_path_outside_job_identity(tmp_path: Path) -> None:
    output_root = tmp_path / "book"
    owned = tmp_path / "mp3" / "book" / "04-01 - Section Alpha.mp3"
    neighbor = tmp_path / "mp3" / "other" / "keep-me.mp3"
    for path in (owned, neighbor):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")

    with pytest.raises(cli_resume.ResumeCheckpointError, match="not owned"):
        cli_render_cleanup.remove_uncommitted_render_artifacts(
            base_output_dir=tmp_path,
            output_root=output_root,
            final_stem_override="04-Section Alpha",
            committed_files=[neighbor],
        )

    assert owned.read_bytes() == b"audio"
    assert neighbor.read_bytes() == b"audio"


def test_render_reset_recognizes_custom_name_numbered_parts(tmp_path: Path) -> None:
    output_root = tmp_path / "book"
    manifest = output_root / "Custom Name.json"
    checkpoint = output_root / "Custom Name.resume.json"
    chunk_dir = output_root / "Custom Name-chunks"
    owned_mp3 = tmp_path / "mp3" / "book" / "02-Custom Name.mp3"
    owned_wav = tmp_path / "wav" / "book" / "03-Custom Name.wav"
    neighbor = tmp_path / "mp3" / "book" / "02-Custom Neighbor.mp3"
    for path in (owned_mp3, owned_wav, neighbor):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")

    cli_render_cleanup.reset_render_artifacts(
        base_output_dir=tmp_path,
        output_root=output_root,
        manifest_path=manifest,
        checkpoint_path=checkpoint,
        chunk_dir=chunk_dir,
        final_stem_override="Custom Name",
        include_manifest=False,
    )

    assert not owned_mp3.exists()
    assert not owned_wav.exists()
    assert neighbor.read_bytes() == b"audio"
