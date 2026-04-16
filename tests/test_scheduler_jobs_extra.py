from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from local_tts_renderer import scheduler_jobs as sj
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
        mp3 = tmp / "ok.mp3"
        mp3.write_bytes(b"\x01")
        manifest = tmp / "book" / "01-Neutral.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            '{"parts":[{"mp3_path":"%s","wav_path":null}]}' % mp3.as_posix(),
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
        assert sj.is_job_complete(tmp, job) is True
        status = {"cpu-1": WorkerStatus(idle_since=1)}
        assert sj.cpu_allowed_chunk_budget(status, "cpu-1") >= 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_build_jobs_md_and_epub_paths(monkeypatch) -> None:
    tmp = _mk_tmp_dir()
    try:
        out = tmp / "out"
        src_md = tmp / "neutral.md"
        src_md.write_text("# Head\nText", encoding="utf-8")
        src_epub = tmp / "neutral.epub"
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

        monkeypatch.setattr(sj, "load_source", fake_load_source)
        monkeypatch.setattr(sj, "is_job_complete", lambda *_a, **_k: False)

        jobs, skipped, cache_map = sj.build_jobs([src_md, src_epub], out, fresh=False, debug=False)
        assert len(jobs) == 3
        assert len(skipped) == 0
        assert src_md in cache_map and src_epub in cache_map
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
