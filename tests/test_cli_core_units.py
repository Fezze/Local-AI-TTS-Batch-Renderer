from __future__ import annotations

import io
import shutil
import types
import uuid
from pathlib import Path

import numpy as np

from local_tts_renderer import cli_audio_utils, cli_chunking_utils, cli_render_flow, cli_runtime, document_helpers
from local_tts_renderer.cli_models import AudioMetadata
from local_tts_renderer.sources import markdown

cli = types.SimpleNamespace(
    AudioMetadata=AudioMetadata,
    OutputPartWriter=cli_render_flow.OutputPartWriter,
    build_chunks=cli_chunking_utils.build_chunks,
    build_output_paths=cli_audio_utils.build_output_paths,
    clean_markdown=markdown.clean_markdown,
    clean_plain_text=document_helpers.clean_plain_text,
    compute_part_output_paths=cli_audio_utils.compute_part_output_paths,
    configure_runtime_temp_dir=cli_runtime.configure_runtime_temp_dir,
    create_audio_with_retry=cli_audio_utils.create_audio_with_retry,
    cross_process_io_gate=cli_audio_utils.cross_process_io_gate,
    extract_track_number=cli_audio_utils.extract_track_number,
    iter_sections=cli_chunking_utils.iter_sections,
    light_trim_audio=cli_audio_utils.light_trim_audio,
    parse_args=cli_runtime.parse_args,
    safe_remove_path=cli_audio_utils.safe_remove_path,
    split_markdown_chapters=markdown.split_markdown_chapters,
    split_sentences=cli_chunking_utils.split_sentences,
    split_text_for_retry=cli_chunking_utils.split_text_for_retry,
    write_mp3_from_audio=cli_audio_utils.write_mp3_from_audio,
    write_mp3_from_wav=cli_audio_utils.write_mp3_from_wav,
)


def _mk_tmp_dir() -> Path:
    path = Path.cwd() / ".test_tmp" / f"cli-core-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_parse_args_minimal(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["md_to_audio.py", "--input", "neutral.md"],
    )
    args = cli.parse_args()
    assert args.input == ["neutral.md"]


def test_parse_args_allows_disabling_mp3_only(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["md_to_audio.py", "--input", "neutral.md", "--no-mp3-only"],
    )
    args = cli.parse_args()
    assert args.mp3_only is False


def test_parse_args_accepts_markdown_chapter_heading_level(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["md_to_audio.py", "--input", "neutral.md", "--md-chapter-heading-level", "2"],
    )
    args = cli.parse_args()
    assert args.md_chapter_heading_level == 2


def test_text_processing_helpers() -> None:
    md = "# Intro\nHello [x](https://e) `code`"
    cleaned = cli.clean_markdown(md)
    assert "Hello" in cleaned
    assert "`" not in cleaned
    chapters = cli.split_markdown_chapters("# Neutral\nA\n\n# Next\nB", "Fallback")
    assert len(chapters) == 2
    assert cli.clean_plain_text("A   B\n\n\nC") == "A B\n\nC"


def test_chunking_helpers() -> None:
    sections = list(cli.iter_sections("HEADING\nLine one.\nLine two.\n\nNEXT\nLine three."))
    assert len(sections) >= 1
    sentences = cli.split_sentences("One. Two? Three!")
    assert len(sentences) == 3
    retry_parts = cli.split_text_for_retry("One two three four")
    assert len(retry_parts) == 2
    chunks = cli.build_chunks("Heading\nThis is a sentence. Another sentence follows.", max_chars=25)
    assert chunks


def test_audio_trim_and_retry() -> None:
    audio = np.array([0.0, 0.0, 0.01, 0.02, 0.0], dtype=np.float32)
    trimmed = cli.light_trim_audio(audio, sample_rate=1000, threshold=0.005, padding_ms=0)
    assert trimmed.size >= 2

    class FakeKokoro:
        def __init__(self):
            self.first = True

        def create(self, text, **kwargs):  # type: ignore[no-untyped-def]
            if self.first and len(text) > 20:
                self.first = False
                raise RuntimeError("bad allocation")
            return np.zeros(100, dtype=np.float32), 24000

    parts, sr = cli.create_audio_with_retry(
        kokoro=FakeKokoro(),
        text="This is a neutral long sentence for retry path coverage.",
        voice="v",
        speed=1.0,
        lang="en-us",
        trim_mode="off",
    )
    assert sr == 24000
    assert len(parts) >= 1


def test_output_path_helpers_and_gate() -> None:
    tmp = _mk_tmp_dir()
    try:
        out_root = tmp / "book" / "chapter"
        wav_mp3 = cli.build_output_paths(out_root, part_count=2)
        assert len(wav_mp3) == 2
        assert cli.extract_track_number("04-02 - Neutral", fallback=9) == 4
        assert cli.extract_track_number("Neutral", fallback=9) == 9

        lock = tmp / ".lock"
        with cli.cross_process_io_gate(lock):
            assert lock.exists()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_runtime_temp_and_file_helpers() -> None:
    tmp = _mk_tmp_dir()
    try:
        rt = cli.configure_runtime_temp_dir(output_dir=tmp, temp_dir=str(tmp / "runtime"))
        assert rt.exists()

        p = tmp / "x.tmp"
        p.write_text("x", encoding="utf-8")
        assert cli.safe_remove_path(p)
        assert not p.exists()
        assert cli.safe_remove_path(p)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_mp3_write_from_audio_and_wav() -> None:
    tmp = _mk_tmp_dir()
    try:
        audio = np.zeros(8000, dtype=np.float32)
        mp3_a = tmp / "a.mp3"
        cli.write_mp3_from_audio(audio, sample_rate=8000, mp3_path=mp3_a, bitrate_kbps=64, force=True)
        assert mp3_a.exists() and mp3_a.stat().st_size > 0

        wav = tmp / "a.wav"
        import soundfile as sf

        sf.write(str(wav), audio, 8000)
        mp3_b = tmp / "b.mp3"
        cli.write_mp3_from_wav(wav, mp3_b, bitrate_kbps=64, force=True)
        assert mp3_b.exists() and mp3_b.stat().st_size > 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_output_part_writer_close(monkeypatch) -> None:
    tmp = _mk_tmp_dir()
    try:
        monkeypatch.setattr(cli_render_flow, "write_mp3_tags", lambda *a, **k: None)
        writer = cli.OutputPartWriter(
            output_root=tmp / "book" / "04-Neutral Chapter",
            base_output_dir=tmp,
            part_index=1,
            multi_part=False,
            sample_rate=24000,
            force=True,
            group_name=None,
            audio_metadata=cli.AudioMetadata(source_title="Neutral Source"),
            mp3_only=True,
            final_stem_override="04-Neutral Chapter",
        )
        writer.chapter_titles = ["Neutral Chapter"]
        writer.start_chunk = 1
        writer.end_chunk = 1
        writer.write_audio(np.zeros(200, dtype=np.float32))
        payload = writer.close(force_numbered_first_part=True)
        assert payload["mp3_path"]
        assert Path(payload["mp3_path"]).exists()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_output_part_writer_fails_fast_when_output_exists(monkeypatch) -> None:
    tmp = _mk_tmp_dir()
    try:
        monkeypatch.setattr(cli_render_flow, "write_mp3_tags", lambda *a, **k: None)
        out_root = tmp / "book" / "04-Neutral Chapter"
        _, mp3_path = cli.compute_part_output_paths(
            output_root=out_root,
            base_output_dir=tmp,
            part_index=1,
            multi_part=False,
            base_name="04-Neutral Chapter",
            group_name=None,
            final_stem_override="04-Neutral Chapter",
        )
        mp3_path.parent.mkdir(parents=True, exist_ok=True)
        mp3_path.write_bytes(b"existing")
        calls: list[Path] = []
        monkeypatch.setattr(cli_render_flow, "safe_remove_path", lambda path: calls.append(path) or True)
        try:
            cli.OutputPartWriter(
                output_root=out_root,
                base_output_dir=tmp,
                part_index=1,
                multi_part=False,
                sample_rate=24000,
                force=False,
                group_name=None,
                audio_metadata=cli.AudioMetadata(source_title="Neutral Source"),
                mp3_only=True,
                final_stem_override="04-Neutral Chapter",
            )
            raise AssertionError("expected FileExistsError")
        except FileExistsError:
            pass
        assert calls == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
