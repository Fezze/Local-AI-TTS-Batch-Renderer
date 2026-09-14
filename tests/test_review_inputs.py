from pathlib import Path
import sys

import pytest

from local_tts_renderer import cli_entry, cli_runtime, scheduler_args, scheduler_core
from local_tts_renderer.input_paths import source_cache_key
from local_tts_renderer.scheduler_jobs import build_jobs


@pytest.mark.parametrize('names', [('a/book.md', 'b/book.md'), ('a-b.md', 'a_b.md'), ('book.md', 'book.epub')])
def test_collision_rejected_before_writes(tmp_path, names):
    paths = [tmp_path / name for name in names]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# Intro\nText')
    out = tmp_path / 'out'
    with pytest.raises(ValueError, match='collision') as error:
        build_jobs(paths, out, fresh=True, force=True)
    assert all(str(path) in str(error.value) for path in paths)
    assert not out.exists()


@pytest.mark.parametrize('entry', [cli_entry, scheduler_core])
def test_cli_collision_exit_code(tmp_path, monkeypatch, entry):
    paths = [tmp_path / name for name in ['a-b.md', 'a_b.md']]
    for path in paths:
        path.write_text('# Intro\nText')
    out = tmp_path / 'out'
    monkeypatch.setattr(sys, 'argv', ['tts', '--input', *map(str, paths), '--out', str(out), '--fresh'])
    assert entry.main() == 2
    assert not out.exists()


def test_cache_keys_do_not_normalize_distinct_paths_together(tmp_path):
    a, b = tmp_path / 'a-b/book.md', tmp_path / 'a_b/book.md'
    assert source_cache_key(a) != source_cache_key(b)
    assert source_cache_key(a) == source_cache_key(a.parent / '.' / a.name)
    assert len(source_cache_key(tmp_path / ('a' * 240 + '.md'))) < 200


@pytest.mark.parametrize('entry', [cli_entry, scheduler_args])
def test_absolute_relative_globs_order_and_dedup(tmp_path, monkeypatch, entry):
    for name in ['b.md', 'a.md']:
        (tmp_path / name).write_text('Text')
    monkeypatch.chdir(tmp_path)
    assert entry.expand_inputs([str(tmp_path / '*.md'), '*.md', 'a.md']) == [tmp_path / 'a.md', tmp_path / 'b.md']
    assert entry.expand_inputs([str(tmp_path / '*.epub')]) == []


@pytest.mark.parametrize('parser', [cli_runtime, scheduler_args])
def test_default_output(parser, monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['tts', '--input', 'book.md'])
    assert parser.parse_args().output_dir == 'out'
