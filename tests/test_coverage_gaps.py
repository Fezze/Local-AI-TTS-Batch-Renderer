from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
import uuid
import types
import zipfile
import threading
from pathlib import Path

from local_tts_renderer import cli_cache, cli_entry, cli_presentation, cli_runtime, cli_parsing, scheduler_jobs, scheduler_process
from local_tts_renderer.cli_models import AudioMetadata, GROUP_PATH_SEPARATOR
from local_tts_renderer.input_parsers import Chapter, TocNode, get_group_leaf_title
from local_tts_renderer.scheduler_types import ChapterJob, WorkerConfig, WorkerStatus


def _scratch_dir(name: str) -> Path:
    path = Path.cwd() / ".test_tmp" / f"{name}-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_cli_runtime_debug_and_temp_dir(monkeypatch) -> None:
    monkeypatch.delenv("LOCAL_TTS_DEBUG", raising=False)
    assert cli_runtime.is_debug_enabled() is False
    monkeypatch.setenv("LOCAL_TTS_DEBUG", "yes")
    assert cli_runtime.is_debug_enabled() is True

    runtime_root = _scratch_dir("coverage-runtime")
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("LOCAL_TTS_TEMP_DIR", str(runtime_root))
    old_tempdir = tempfile.tempdir
    try:
        resolved = cli_runtime.configure_runtime_temp_dir(output_dir=runtime_root)
        assert resolved.exists()
        assert os.environ["TMPDIR"] == str(resolved)
    finally:
        tempfile.tempdir = old_tempdir


def test_cli_runtime_heartbeat_and_ensure_file(monkeypatch) -> None:
    progress_state = {"chapter_index": 2, "chapter_title": "T", "completed_chunks": 3, "total_chunks": 7}
    stop_event, thread = cli_runtime.start_progress_heartbeat(progress_state, interval_seconds=0)
    assert thread is None
    assert stop_event.is_set() is False

    calls: list[tuple[str, bool, int]] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            calls.append(("iter", True, chunk_size))
            yield b"abc"

    monkeypatch.setattr(cli_runtime.requests, "get", lambda *a, **k: Response())
    target = _scratch_dir("coverage-file") / "file.bin"
    cli_runtime.ensure_file(target, "https://example.test/file.bin")
    assert target.read_bytes() == b"abc"
    assert calls == [("iter", True, 1024 * 1024)]


def test_cli_entry_expand_inputs_and_main_branches(monkeypatch) -> None:
    tmp_path = _scratch_dir("coverage-inputs")
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("x", encoding="utf-8")
    b.write_text("y", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    expanded = cli_entry.expand_inputs([str(a), "*.txt"])
    assert expanded == [a.resolve(), b.resolve()]

    wav = tmp_path / "in.wav"
    wav.write_bytes(b"wav")
    monkeypatch.setattr(
        cli_entry,
        "parse_args",
        lambda: argparse.Namespace(
            wav_to_mp3=str(wav),
            mp3_bitrate=128,
            force=False,
            input=None,
            output_dir=None,
            list_chapters=False,
        ),
    )
    monkeypatch.setattr(cli_entry, "write_mp3_from_wav", lambda **k: wav.with_suffix(".mp3"))
    assert cli_entry.main() == 0


def test_cli_entry_md_flags(monkeypatch) -> None:
    source = _scratch_dir("md-flags") / "doc.md"
    source.write_text("# A\none\n\n# B\ntwo", encoding="utf-8")
    out_dir = _scratch_dir("md-flags-out")
    seen: list[list[Chapter]] = []

    monkeypatch.setattr(cli_entry, "parse_args", lambda: argparse.Namespace(
        wav_to_mp3=None,
        input=[str(source)],
        output_dir=str(out_dir),
        list_chapters=False,
        model_dir="models",
        voice="v",
        lang="en",
        speed=1.0,
        max_chars=100,
        silence_ms=50,
        max_part_minutes=1.0,
        keep_chunks=False,
        mp3_only=True,
        force=False,
        chapter_index=None,
        chapter_cache=None,
        output_subdir=None,
        output_name=None,
        md_single_chapter=True,
        max_chapter_chars=0,
        trim_mode="off",
        heartbeat_seconds=0.0,
        providers=None,
        temp_dir=None,
        warmup_text="",
        max_parts_per_run=0,
    ))
    monkeypatch.setattr(cli_entry, "load_chapters", lambda path, **kwargs: seen.append([Chapter(title="Whole", text="x", group=None)]) or seen[-1])
    monkeypatch.setattr(cli_entry, "build_group_directory_map", lambda chapters: {})
    monkeypatch.setattr(cli_entry, "render_audio", lambda **kwargs: {"parts": [], "chunk_count": 1, "voice": "v"})
    monkeypatch.setattr(cli_entry, "extract_epub_metadata", lambda path: AudioMetadata(source_title="s"))
    assert cli_entry.main() == 0
    assert seen


def test_cli_entry_list_and_partial_branches(monkeypatch) -> None:
    source = _scratch_dir("entry-source") / "doc.md"
    source.write_text("# A\nbody", encoding="utf-8")
    out_dir = _scratch_dir("entry-out")

    common = dict(
        wav_to_mp3=None,
        input=[str(source)],
        output_dir=str(out_dir),
        list_chapters=True,
        model_dir="models",
        voice="v",
        lang="en",
        speed=1.0,
        max_chars=100,
        silence_ms=50,
        max_part_minutes=1.0,
        keep_chunks=False,
        mp3_only=True,
        force=False,
        chapter_index=None,
        chapter_cache=None,
        output_subdir=None,
        output_name=None,
        trim_mode="off",
        heartbeat_seconds=0.0,
        providers=None,
        temp_dir=None,
        warmup_text="",
        max_parts_per_run=0,
    )
    monkeypatch.setattr(cli_entry, "parse_args", lambda: argparse.Namespace(**common))
    monkeypatch.setattr(cli_entry, "load_chapters", lambda path: [Chapter(title="T", text="alpha", group=None)])
    monkeypatch.setattr(cli_entry, "print_chapter_summary", lambda *a, **k: None)
    monkeypatch.setattr(cli_entry, "print_output_structure_preview", lambda *a, **k: None)
    monkeypatch.setattr(cli_entry, "print_toc_tree", lambda *a, **k: None)
    monkeypatch.setattr(cli_entry, "load_epub_toc_from_path", lambda path: [])
    assert cli_entry.main() == 0

    common["list_chapters"] = False
    common["chapter_index"] = 1
    common["output_dir"] = str(_scratch_dir("entry-out2"))
    monkeypatch.setattr(cli_entry, "parse_args", lambda: argparse.Namespace(**common))
    monkeypatch.setattr(cli_entry, "build_group_directory_map", lambda chapters: {})
    monkeypatch.setattr(cli_entry, "extract_epub_metadata", lambda path: AudioMetadata(source_title="s"))
    monkeypatch.setattr(cli_entry, "render_audio", lambda **kwargs: (_ for _ in ()).throw(cli_entry.PartialRunComplete()))
    assert cli_entry.main() == 75


def test_cli_parsing_helpers_and_preview(monkeypatch) -> None:
    assert cli_parsing.strip_front_matter("---\na: b\n---\nbody") == "body"

    payload = _scratch_dir("coverage-cache") / "cache.json"
    payload.write_text(json.dumps([{"title": "A", "text": "hello world", "group": "g"}]), encoding="utf-8")
    chapters = cli_cache.load_chapters_from_cache(payload)
    assert chapters[0].title == "A"
    assert get_group_leaf_title(None) == "Chapter"
    assert get_group_leaf_title(f"one{GROUP_PATH_SEPARATOR}two") == "two"
    assert cli_presentation.summarize_chapters(chapters)[0]["words"] == 2

    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    cli_presentation.print_toc_tree([TocNode(title="Root", href="x", children=[TocNode(title="Child")])])
    assert "- Root -> x" in buf.getvalue()


def test_cli_parsing_metadata_and_preview(monkeypatch) -> None:
    epub = _scratch_dir("coverage-եպub") / "book.epub"
    epub.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(epub, "w") as archive:
        archive.writestr(
            "META-INF/container.xml",
            "<?xml version='1.0'?><container><rootfiles><rootfile full-path='pkg.opf'/></rootfiles></container>",
        )
        archive.writestr(
            "pkg.opf",
            "<?xml version='1.0'?><package><metadata><title>Book</title><creator>A</creator><publisher>P</publisher><date>2024</date><language>en</language></metadata></package>",
        )
    meta = cli_parsing.extract_epub_metadata(epub)
    assert meta.source_title == "Book"

    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    cli_presentation.print_output_structure_preview(
        Path("story.md"),
        [Chapter(title="One", text="x", group=None), Chapter(title="Two", text="y", group="g/a")],
    )
    assert "out/story/" in buf.getvalue()


def test_scheduler_process_and_jobs_branches(monkeypatch) -> None:
    tmp_path = _scratch_dir("coverage-jobs")
    proc = type("P", (), {"pid": 123, "poll": lambda self: 0})()
    scheduler_process.register_process("w", proc)  # type: ignore[arg-type]
    assert scheduler_process.terminate_active_process("missing") is False
    scheduler_process.unregister_process("w")

    job = ChapterJob(
        source_path=tmp_path / "source.md",
        chapter_index=1,
        chapter_title="Short intro",
        output_subdir="out",
        output_name="01-out",
        estimated_chars=10,
        estimated_chunks=1,
    )
    worker = WorkerConfig(name="cpu-1", provider="CPUExecutionProvider")
    statuses = {"cpu-1": WorkerStatus(idle_since=0.0)}
    args = argparse.Namespace(
        output_dir=str(tmp_path),
        model_dir=str(tmp_path / "models"),
        voice="v",
        speed=1.0,
        max_part_minutes=1.0,
        silence_ms=50,
        trim_mode="off",
        heartbeat_seconds=0.0,
        warmup_text="",
        force=True,
        keep_chunks=True,
        mp3_only=True,
        max_parts_per_run=2,
        cpu_worker_max_chars=100,
        gpu_large_chapter_max_chars=200,
        gpu_small_chapter_max_chars=150,
        aggressive_gpu_recovery=True,
        cpu_max_chars=100,
        gpu_short_first=False,
    )
    assert scheduler_jobs.choose_worker_max_chars(worker, job, args) == 350
    command = scheduler_jobs.build_worker_command(
        python_exe=Path("python"),
        script_path=Path("md_to_audio.py"),
        args=args,
        source_path=job.source_path,
        job=job,
        worker_max_chars=120,
        cache_path=None,
    )
    assert "--force" in command and "--keep-chunks" in command and "--max-parts-per-run" in command
    assert scheduler_jobs.select_next_job([job], worker, statuses, cpu_max_chars=100, gpu_short_first=False) == 0


def test_cli_runtime_provider_and_espeak(monkeypatch, tmp_path: Path) -> None:
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    touched: list[Path] = []
    monkeypatch.setattr(cli_runtime, "ensure_file", lambda path, url: touched.append(path))
    model_path, voices_path = cli_runtime.ensure_model_files(model_dir)
    assert touched == [model_path, voices_path]

    fake_ort = types.SimpleNamespace(get_available_providers=lambda: ["CPUExecutionProvider"])
    monkeypatch.setattr(cli_runtime, "_ORT", fake_ort)
    monkeypatch.delenv("ONNX_PROVIDER", raising=False)
    assert cli_runtime.configure_onnx_provider(["CPUExecutionProvider"]) == "CPUExecutionProvider"

    class FakeEspeakAPI:
        _local_tts_patch_enabled = False

        def __init__(self, library, data_path):
            raise PermissionError("blocked")

        @staticmethod
        def _delete(library, tempdir):
            return None

        @staticmethod
        def _shared_library_path(lib):
            return Path("libespeak.dll")

        def _delete_win32(self):
            return None

    fake_api = types.SimpleNamespace(EspeakAPI=FakeEspeakAPI)
    monkeypatch.setitem(sys.modules, "phonemizer", types.SimpleNamespace(backend=types.SimpleNamespace(espeak=types.SimpleNamespace(api=fake_api))))
    monkeypatch.setitem(sys.modules, "phonemizer.backend", types.SimpleNamespace(espeak=types.SimpleNamespace(api=fake_api)))
    monkeypatch.setitem(sys.modules, "phonemizer.backend.espeak", types.SimpleNamespace(api=fake_api))
    monkeypatch.setitem(sys.modules, "phonemizer.backend.espeak.api", fake_api)

    class FakeLib:
        def __init__(self):
            self.espeak_Initialize = lambda *a: 1

    monkeypatch.setattr(cli_runtime.ctypes.cdll, "LoadLibrary", lambda path: FakeLib())
    monkeypatch.setattr(cli_runtime.os, "name", "nt", raising=False)
    cli_runtime.enable_windows_espeak_fallback()
    assert FakeEspeakAPI._local_tts_patch_enabled is True


def test_scheduler_process_controls(monkeypatch) -> None:
    real_thread_cls = threading.Thread
    proc = type("P", (), {"pid": 123, "poll": lambda self: None})()
    killed: list[tuple[str, bool]] = []
    monkeypatch.setattr(scheduler_process.subprocess, "run", lambda *a, **k: killed.append(("taskkill", True)))
    monkeypatch.setattr(scheduler_process.os, "name", "nt", raising=False)
    scheduler_process.terminate_process_tree(proc, force=True)
    assert killed

    calls: list[str] = []

    class FakeMsvcrt:
        seq = iter(["p", "r", "1", "h"])
        done = False

        @staticmethod
        def kbhit():
            return not FakeMsvcrt.done

        @staticmethod
        def getwch():
            key = next(FakeMsvcrt.seq, "")
            if not key:
                FakeMsvcrt.done = True
            return key

    class FakeThread:
        def __init__(self, target, name, daemon):
            self.target = target

        def start(self):
            stop_event = None
            if self.target.__closure__:
                for cell in self.target.__closure__:
                    if isinstance(cell.cell_contents, threading.Event):
                        stop_event = cell.cell_contents
                        break
            worker = real_thread_cls(target=self.target, daemon=True)
            worker.start()
            if stop_event is None:
                worker.join(timeout=2)
                return
            import time as _time

            _time.sleep(0.05)
            stop_event.set()
            worker.join(timeout=2)

    monkeypatch.setitem(sys.modules, "msvcrt", FakeMsvcrt)
    monkeypatch.setattr(scheduler_process.threading, "Thread", FakeThread)
    monkeypatch.setattr(scheduler_process, "terminate_all_active_processes", lambda force=True: calls.append("all"))
    monkeypatch.setattr(scheduler_process, "terminate_active_process", lambda name, force=True: calls.append(name) or True)
    cond = threading.Condition()
    workers = [WorkerConfig(name="gpu-1", provider="CUDAExecutionProvider")]
    stop_event, thread = scheduler_process.start_console_controls(cond, workers, enabled=True)
    assert thread is not None
    assert calls


def test_scheduler_process_debug_toggle(monkeypatch) -> None:
    real_thread_cls = threading.Thread
    monkeypatch.setattr(scheduler_process.os, "name", "nt", raising=False)
    monkeypatch.delenv("LOCAL_TTS_DEBUG", raising=False)

    class FakeMsvcrt:
        seq = iter(["d"])

        @staticmethod
        def kbhit():
            return True

        @staticmethod
        def getwch():
            return next(FakeMsvcrt.seq, "")

    class FakeThread:
        def __init__(self, target, name, daemon):
            self.target = target

        def start(self):
            worker = real_thread_cls(target=self.target, daemon=True)
            worker.start()
            import time as _time

            _time.sleep(0.02)
            worker_event = None
            if self.target.__closure__:
                for cell in self.target.__closure__:
                    if isinstance(cell.cell_contents, threading.Event):
                        worker_event = cell.cell_contents
                        break
            if worker_event is not None:
                worker_event.set()
            worker.join(timeout=2)

    monkeypatch.setitem(sys.modules, "msvcrt", FakeMsvcrt)
    monkeypatch.setattr(scheduler_process.threading, "Thread", FakeThread)
    cond = threading.Condition()
    stop_event, thread = scheduler_process.start_console_controls(cond, [WorkerConfig(name="gpu-1", provider="CUDAExecutionProvider")], enabled=True)
    assert thread is not None
    assert "LOCAL_TTS_DEBUG" in os.environ
