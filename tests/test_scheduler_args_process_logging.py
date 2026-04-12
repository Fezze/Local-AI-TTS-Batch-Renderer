from __future__ import annotations

import io
import queue
import shutil
import signal
import uuid
from argparse import Namespace
from pathlib import Path

from local_tts_renderer import scheduler_args as sargs
from local_tts_renderer import scheduler_logging as slog
from local_tts_renderer import scheduler_process as sproc
from local_tts_renderer.scheduler_types import WorkerConfig, WorkerStatus


def _mk_tmp_dir() -> Path:
    path = Path.cwd() / ".test_tmp" / f"sched-mod-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_expand_inputs_directory_and_glob() -> None:
    tmp = _mk_tmp_dir()
    try:
        (tmp / "a.md").write_text("x", encoding="utf-8")
        (tmp / "b.epub").write_text("x", encoding="utf-8")
        (tmp / "c.txt").write_text("x", encoding="utf-8")
        from_dir = sargs.expand_inputs([str(tmp)])
        rel_pattern = str((tmp / "*.md").relative_to(Path.cwd()))
        from_glob = sargs.expand_inputs([rel_pattern])
        assert any(p.name == "a.md" for p in from_dir)
        assert any(p.name == "b.epub" for p in from_dir)
        assert all(p.suffix in {".md", ".epub"} for p in from_dir)
        assert [p.name for p in from_glob] == ["a.md"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_scheduler_logging_helpers(capsys) -> None:
    payload = slog.parse_heartbeat_line('{"heartbeat": true, "completed_chunks": 1}')
    assert payload and payload["heartbeat"] is True
    assert slog.parse_worker_done_line('{"worker_job_done": true}') is not None
    assert slog.update_worker_phase("spawn", "[run:bootstrap] loading onnxruntime...") == "bootstrap_onnxruntime"
    args = Namespace(worker_silence_timeout_seconds=120, bootstrap_silence_timeout_seconds=30)
    assert slog.resolve_worker_silence_timeout(args, "warmup") == 30
    assert slog.resolve_worker_silence_timeout(args, "render") == 120
    assert slog.format_seconds(3661) == "1h01m"

    statuses = {"gpu-1": WorkerStatus(active=False)}
    slog.print_batch_summary(statuses, total_jobs=2, done_jobs=1, failed_jobs=0, completed_chunks=4, total_chunks=8, started_at=0.0)
    out = capsys.readouterr().out
    assert "[batch] done 1/2" in out


def test_start_stdout_reader_and_progress_print(capsys) -> None:
    q: queue.Queue[str | None] = queue.Queue()
    stream = io.StringIO("line1\nline2\n")
    t = slog.start_stdout_reader(stream, q)
    t.join(timeout=2)
    vals = []
    while not q.empty():
        vals.append(q.get())
    assert "line1\n" in vals
    slog.print_worker_progress("gpu-1", "Neutral", "[1/2] 50.0% chapter=1/1 chunk=1 chars=100 chunk_time=1.0s elapsed=2.0s eta=2.0s")
    assert "[gpu-1] Neutral" in capsys.readouterr().out


def test_scheduler_process_registry_and_terminate(monkeypatch) -> None:
    class P:
        pid = 111

        def poll(self):
            return None

    p = P()
    calls: list[tuple[int, bool]] = []

    def fake_terminate(proc, force=False):
        calls.append((proc.pid, force))

    monkeypatch.setattr(sproc, "terminate_process_tree", fake_terminate)
    sproc.register_process("gpu-1", p)  # type: ignore[arg-type]
    assert sproc.terminate_active_process("gpu-1", force=True) is True
    sproc.terminate_all_active_processes(force=False)
    sproc.unregister_process("gpu-1")
    assert sproc.terminate_active_process("gpu-1") is False
    assert calls[0] == (111, True)


def test_terminate_process_tree_paths(monkeypatch) -> None:
    class P:
        pid = 222

        def poll(self):
            return None

    cmds: list[list[str]] = []
    monkeypatch.setattr(sproc.os, "name", "nt", raising=False)
    monkeypatch.setattr(sproc.subprocess, "run", lambda cmd, **kwargs: cmds.append(cmd))
    sproc.terminate_process_tree(P(), force=True)  # type: ignore[arg-type]
    assert cmds and cmds[0][0] == "taskkill"

    kills: list[tuple[int, int]] = []
    monkeypatch.setattr(sproc.os, "name", "posix", raising=False)
    monkeypatch.setattr(sproc.os, "killpg", lambda pid, sig: kills.append((pid, sig)), raising=False)
    sproc.terminate_process_tree(P(), force=False)  # type: ignore[arg-type]
    assert kills and kills[0] == (222, signal.SIGTERM)


def test_start_console_controls_disabled() -> None:
    stop, thread = sproc.start_console_controls(
        scheduler_condition=__import__("threading").Condition(),
        workers=[WorkerConfig(name="cpu-1", provider="CPUExecutionProvider")],
        enabled=False,
    )
    assert stop.is_set() is False
    assert thread is None
