from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pytest

from local_tts_renderer import scheduler_completion as sc
from local_tts_renderer import scheduler_jobs as sj
from local_tts_renderer import scheduler_scan as ss
from local_tts_renderer.scheduler_types import ChapterJob, WorkerStatus
from local_tts_renderer.sources.model import SourceChapter as Chapter
from local_tts_renderer.sources.model import SourceDocument, SourceMetadata, SourceNavigationNode


def _mk_tmp_dir() -> Path:
    path = Path.cwd() / ".test_tmp" / f"scheduler-jobs-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_is_short_section_title_and_re_slug() -> None:
    assert sj.is_short_section_title("Table of Contents")
    assert not sj.is_short_section_title("Neutral Chapter")
    assert sj.re_slug(" Hello / World ") == "hello-world"


def test_clear_directory_contents() -> None:
    tmp = _mk_tmp_dir()
    try:
        d = tmp / "x"
        d.mkdir()
        (d / "a.txt").write_text("a", encoding="utf-8")
        (d / "inner").mkdir()
        (d / "inner" / "b.txt").write_text("b", encoding="utf-8")
        sj.clear_directory_contents(d)
        assert list(d.iterdir()) == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_prepare_worker_temp_dirs() -> None:
    workers = [sj.WorkerConfig(name="gpu-1", provider="CUDAExecutionProvider")]
    root, mapping = sj.prepare_worker_temp_dirs(workers)
    assert root.exists()
    assert "gpu-1" in mapping


def test_is_job_complete_and_cpu_budget() -> None:
    tmp = _mk_tmp_dir()
    try:
        mp3 = tmp / "mp3" / "book" / "01-Neutral.mp3"
        mp3.parent.mkdir(parents=True)
        mp3.write_bytes(b"\x01")
        manifest = tmp / "book" / "01-Neutral.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps(
                {
                    "chapter_count": 1,
                    "chunk_count": 1,
                    "parts": [
                        {
                            "part": 1,
                            "mp3_path": str(mp3),
                            "wav_path": None,
                            "start_chunk": 1,
                            "end_chunk": 1,
                            "group": None,
                        }
                    ],
                    "chunks": [
                        {
                            "index": 1,
                            "heading": "Neutral",
                            "chapter": "Neutral",
                            "chars": len("Current source text."),
                            "text": "Current source text.",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        job = ChapterJob(
            source_path=tmp / "src.md",
            chapter_index=1,
            chapter_title="Neutral",
            output_subdir="book",
            output_name="01-Neutral",
            estimated_chars=100,
            estimated_chunks=1,
        )
        assert sj.is_job_complete(
            tmp,
            job,
            expected_chapter=Chapter(title="Neutral", text="Current source text."),
            expected_document_chapters=[Chapter(title="Neutral", text="Current source text.")],
        ) is True
        assert sj.is_job_complete(
            tmp,
            job,
            expected_chapter=Chapter(title="Neutral", text="Changed source text."),
            expected_document_chapters=[Chapter(title="Neutral", text="Changed source text.")],
        ) is False
        status = {"cpu-1": WorkerStatus(idle_since=1)}
        assert sj.cpu_allowed_chunk_budget(status, "cpu-1") >= 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_is_job_complete_reuses_a_manifest_check(monkeypatch) -> None:
    job = ChapterJob(Path("book.md"), 1, "One", "book", "01-One", 5, 1)
    chapter = Chapter(title="One", text="Text.")
    calls = 0

    def fake_load_complete_manifest(**_kwargs):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return {"complete": True}

    monkeypatch.setattr(sc, "load_complete_manifest", fake_load_complete_manifest)
    monkeypatch.setattr(Path, "exists", lambda _self: True)
    completion_cache: sc.CompletionCache = {}

    assert sc.is_job_complete(Path("out"), job, chapter, [chapter], completion_cache)
    assert sc.is_job_complete(Path("out"), job, chapter, [chapter], completion_cache)
    assert calls == 1


def test_is_job_complete_falls_back_to_valid_full_source_manifest() -> None:
    tmp = _mk_tmp_dir()
    try:
        source = tmp / "book.md"
        chapters = [
            Chapter(title="One", text="First.", group=None),
            Chapter(title="Two", text="Second.", group=None),
        ]
        job = ChapterJob(
            source_path=source,
            chapter_index=1,
            chapter_title="One",
            output_subdir="book",
            output_name="01-One",
            estimated_chars=6,
            estimated_chunks=1,
        )
        mp3 = tmp / "mp3" / "book" / "book.mp3"
        mp3.parent.mkdir(parents=True)
        mp3.write_bytes(b"audio")
        full_manifest = tmp / "book.json"
        full_manifest.write_text(
            json.dumps(
                {
                    "chapter_count": 2,
                    "chunk_count": 2,
                    "parts": [
                        {
                            "part": 1,
                            "mp3_path": str(mp3),
                            "wav_path": None,
                            "start_chunk": 1,
                            "end_chunk": 2,
                            "group": None,
                        }
                    ],
                    "chunks": [
                        {
                            "index": index,
                            "heading": chapter.title,
                            "chapter": chapter.title,
                            "chars": len(chapter.text),
                            "text": chapter.text,
                        }
                        for index, chapter in enumerate(chapters, start=1)
                    ],
                }
            ),
            encoding="utf-8",
        )
        corrupt_primary = tmp / "book" / "01-One.json"
        corrupt_primary.parent.mkdir(parents=True)
        corrupt_primary.write_text("{", encoding="utf-8")

        assert sj.is_job_complete(
            tmp,
            job,
            expected_chapter=chapters[0],
            expected_document_chapters=chapters,
        ) is True
        changed_chapters = [chapters[0], Chapter(title="Two", text="Changed.", group=None)]
        assert sj.is_job_complete(
            tmp,
            job,
            expected_chapter=changed_chapters[0],
            expected_document_chapters=changed_chapters,
        ) is False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_build_jobs_md_and_epub_paths(monkeypatch) -> None:
    tmp = _mk_tmp_dir()
    try:
        out = tmp / "out"
        src_md = tmp / "neutral.md"
        src_md.write_text("# Head\nText", encoding="utf-8")
        src_epub = tmp / "other.epub"
        src_epub.write_text("dummy", encoding="utf-8")

        chapters_md = [Chapter(title="Intro", text="text", group=None)]
        chapters_epub = [
            Chapter(title="Chapter 1", text="text", group="Book / Part A"),
            Chapter(title="Chapter 2", text="text", group="Book / Part A"),
        ]

        def fake_load_source(path: Path, options=None):  # type: ignore[no-untyped-def]
            if path.suffix == ".epub":
                return SourceDocument(
                    path=path,
                    metadata=SourceMetadata(source_title="neutral"),
                    chapters=chapters_epub,
                    navigation=[
                        SourceNavigationNode(
                            title="Book",
                            href="x",
                            children=[SourceNavigationNode(title="Part A", href="y")],
                        )
                    ],
                )
            return SourceDocument(path=path, metadata=SourceMetadata(source_title="neutral"), chapters=chapters_md)

        monkeypatch.setattr(ss, "load_source", fake_load_source)
        monkeypatch.setattr(sj, "is_job_complete", lambda *_a, **_k: False)
        monkeypatch.setattr(sj.os, "cpu_count", lambda: 1)

        jobs, skipped, cache_map = sj.build_jobs([src_md, src_epub], out, fresh=False, debug=False)
        assert len(jobs) == 3
        assert len(skipped) == 0
        assert src_md in cache_map and src_epub in cache_map
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_source_scan_cache_reuses_unchanged_document(monkeypatch) -> None:
    tmp = _mk_tmp_dir()
    try:
        source = tmp / "book.md"
        source.write_text("# Head\nText", encoding="utf-8")
        loads = 0
        document = SourceDocument(
            path=source,
            metadata=SourceMetadata(source_title="Book"),
            chapters=[Chapter(title="Intro", text="text", group="Part")],
            navigation=[SourceNavigationNode(title="Part", children=[SourceNavigationNode(title="Intro")])],
        )

        def fake_load_source(*_args, **_kwargs):  # type: ignore[no-untyped-def]
            nonlocal loads
            loads += 1
            return document

        monkeypatch.setattr(ss, "load_source", fake_load_source)
        cache_root = tmp / "cache"
        loaded, cache_hit = ss.load_document_for_jobs(source, cache_root, False, 0, 0)
        cached, cached_hit = ss.load_document_for_jobs(source, cache_root, False, 0, 0)

        assert loads == 1
        assert cache_hit is False
        assert cached_hit is True
        assert cached == loaded
        assert cached.navigation == document.navigation

        source.write_text("# Head\nChanged text", encoding="utf-8")
        _, changed_hit = ss.load_document_for_jobs(source, cache_root, False, 0, 0)

        assert loads == 2
        assert changed_hit is False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_build_jobs_logs_cached_source_without_parse_messages(monkeypatch, capsys) -> None:
    tmp = _mk_tmp_dir()
    try:
        source = tmp / "book.md"
        source.write_text("# Intro\nText", encoding="utf-8")
        monkeypatch.setattr(sj.os, "cpu_count", lambda: 1)

        sj.build_jobs([source], tmp / "out", fresh=False)
        capsys.readouterr()
        sj.build_jobs([source], tmp / "out", fresh=False)
        output = capsys.readouterr().out

        assert "[batch:scan] source_cached" in output
        assert "[batch:scan] chapters_loaded" not in output
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_build_jobs_scans_multiple_sources_with_process_pool() -> None:
    tmp = _mk_tmp_dir()
    try:
        out = tmp / "out"
        sources = [tmp / f"book-{index}.md" for index in range(2)]
        for source in sources:
            source.write_text("# Head\nText", encoding="utf-8")

        jobs, skipped, cache_map = sj.build_jobs(sources, out, fresh=False)

        assert [job.source_path for job in jobs] == sources
        assert not skipped
        assert set(cache_map) == set(sources)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.parametrize("fresh", [False, True])
@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("complete", [False, True])
def test_build_jobs_flag_matrix_skips_completed_unless_forced(monkeypatch, fresh: bool, force: bool, complete: bool) -> None:
    tmp = _mk_tmp_dir()
    try:
        out = tmp / "out"
        source = tmp / "neutral.md"
        source.write_text("# Head\nText", encoding="utf-8")
        document = SourceDocument(
            path=source,
            metadata=SourceMetadata(source_title="neutral"),
            chapters=[Chapter(title="Intro", text="text", group=None)],
        )
        monkeypatch.setattr(ss, "load_source", lambda *_a, **_k: document)
        checked_chapters: list[Chapter] = []

        def fake_is_job_complete(  # type: ignore[no-untyped-def]
            _output_dir,
            _job,
            *,
            expected_chapter,
            expected_document_chapters,
            completion_cache=None,
        ):
            checked_chapters.append(expected_chapter)
            assert expected_document_chapters == document.chapters
            return complete

        monkeypatch.setattr(sj, "is_job_complete", fake_is_job_complete)

        jobs, skipped, _ = sj.build_jobs([source], out, fresh=fresh, force=force)

        assert checked_chapters == document.chapters
        assert len(jobs) == (1 if force or not complete else 0)
        assert len(skipped) == (1 if complete and not force else 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.parametrize(("fresh", "expected_pin"), [(False, 777), (True, None)])
def test_build_jobs_restores_chunk_size_pin_after_scheduler_restart(
    monkeypatch,
    fresh: bool,
    expected_pin: int | None,
) -> None:
    tmp = _mk_tmp_dir()
    try:
        out = tmp / "out"
        source = tmp / "neutral.md"
        source.write_text("# Head\nText", encoding="utf-8")
        document = SourceDocument(
            path=source,
            metadata=SourceMetadata(source_title="neutral"),
            chapters=[Chapter(title="Intro", text="text", group=None)],
        )
        checkpoint = out / "neutral" / "01-Intro.resume.json"
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        checkpoint.write_text(json.dumps({"render_max_chars": 777}), encoding="utf-8")
        monkeypatch.setattr(ss, "load_source", lambda *_a, **_k: document)
        monkeypatch.setattr(sj, "is_job_complete", lambda *_a, **_k: False)

        jobs, skipped, _ = sj.build_jobs([source], out, fresh=fresh)

        assert not skipped
        assert jobs[0].render_max_chars == expected_pin
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
