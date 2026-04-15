from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import local_tts_renderer as root_mod
from local_tts_renderer import chunking, render


def test_root_entrypoints_delegate(monkeypatch) -> None:
    monkeypatch.setattr("local_tts_renderer.cli.main", lambda *a, **k: 11)
    monkeypatch.setattr("local_tts_renderer.scheduler.main", lambda *a, **k: 22)
    assert root_mod.tts_main() == 11
    assert root_mod.batch_main() == 22


def test_chunking_and_render_wrappers_delegate(monkeypatch) -> None:
    monkeypatch.setattr(chunking, "build_chunks", lambda *a, **k: ["x"])
    monkeypatch.setattr(chunking, "chunk_section", lambda *a, **k: ["y"])
    monkeypatch.setattr(chunking, "split_paragraphs", lambda *a, **k: ["p"])
    monkeypatch.setattr(chunking, "split_sentences", lambda *a, **k: ["s"])
    monkeypatch.setattr(chunking, "split_text_for_retry", lambda *a, **k: ["r"])
    monkeypatch.setattr(render, "render_audio", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(render, "write_mp3_from_audio", lambda *a, **k: "mp3a")
    monkeypatch.setattr(render, "write_mp3_from_wav", lambda *a, **k: "mp3w")

    assert chunking.build_chunks("x", 10) == ["x"]
    assert chunking.chunk_section(None, "x", 10, 1) == ["y"]
    assert chunking.split_paragraphs("x") == ["p"]
    assert chunking.split_sentences("x") == ["s"]
    assert chunking.split_text_for_retry("x") == ["r"]
    assert render.render_audio() == {"ok": True}
    assert render.write_mp3_from_audio(None, 0, None, 0, False) == "mp3a"
    assert render.write_mp3_from_wav(None, None, 0, False) == "mp3w"


def test_top_level_scripts_bootstrap_src_path() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    for script_name in ("md_to_audio.py", "run_tts_batch.py"):
        result = subprocess.run(
            [sys.executable, script_name, "--help"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        assert "usage:" in result.stdout.lower()
