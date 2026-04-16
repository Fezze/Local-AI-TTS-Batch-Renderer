from __future__ import annotations

from pathlib import Path

from local_tts_renderer import chunking, cli_core, cli_entry, render, scheduler
from local_tts_renderer.sources.model import SourceChapter, SourceDocument, SourceMetadata, SourceNavigationNode


SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "local_tts_renderer"


def _read_module(relative_path: str) -> str:
    return (SRC_ROOT / relative_path).read_text(encoding="utf-8")


def test_internal_modules_do_not_use_input_parsers_facade() -> None:
    offenders: list[str] = []
    for path in SRC_ROOT.rglob("*.py"):
        if path.name == "input_parsers.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "input_parsers" in text:
            offenders.append(str(path.relative_to(SRC_ROOT)))
    assert offenders == []


def test_orchestration_uses_source_layer_without_legacy_escape_hatches() -> None:
    for module in ("cli_entry.py", "scheduler_jobs.py"):
        text = _read_module(module)
        assert "load_source" in text
        assert "_ORIGINAL_LOAD_CHAPTERS" not in text
        assert "load_chapters is not" not in text
        assert 'suffix.lower() == ".epub"' not in text
        assert 'suffix.lower() == ".md"' not in text


def test_cli_core_stays_minimal() -> None:
    assert cli_core.__all__ == ["main", "parse_args"]


def test_public_compatibility_shims_stay_small() -> None:
    assert render.__all__ == ["render_audio", "write_mp3_from_audio", "write_mp3_from_wav"]
    assert chunking.__all__ == [
        "build_chunks",
        "chunk_section",
        "split_paragraphs",
        "split_sentences",
        "split_text_for_retry",
    ]
    assert scheduler.__all__ == [
        "ChapterJob",
        "WorkerConfig",
        "WorkerStatus",
        "build_worker_command",
        "choose_worker_max_chars",
        "cpu_allowed_chunk_budget",
        "main",
        "parse_args",
        "resolve_worker_silence_timeout",
        "select_next_job",
        "update_worker_phase",
    ]


def test_cached_chapters_keep_document_metadata_and_navigation_context() -> None:
    cached_chapters = [SourceChapter(title="Cached", text="cached text", group="Book / Part")]
    document = SourceDocument(
        path=Path("book.epub"),
        metadata=SourceMetadata(source_title="Real Metadata", author="Author"),
        chapters=cached_chapters,
        navigation=[
            SourceNavigationNode(
                title="Book",
                children=[SourceNavigationNode(title="Part")],
            )
        ],
    )

    metadata = cli_entry._audio_metadata_from_source(document)
    group_map = cli_entry._group_directory_map_for_source(document)

    assert metadata.source_title == "Real Metadata"
    assert metadata.author == "Author"
    assert group_map["Book / Part"] == Path("01-Book")
