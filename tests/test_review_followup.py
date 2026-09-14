import io
import os
import sys
import threading

import pytest

from local_tts_renderer import scheduler_runtime as sr
from local_tts_renderer.scheduler_scan import load_document_for_jobs
from local_tts_renderer.scheduler_types import ChapterJob, WorkerConfig, WorkerStatus
from test_epub_logical_chapters import make_epub, canonical
from test_scheduler_runtime_flow import _base_args


def test_scan_cache_detects_same_size_edit_with_preserved_timestamp(tmp_path):
    source = tmp_path / 'book.md'
    source.write_text('# Title\nAlpha.')
    cache = tmp_path / 'cache'
    first, hit = load_document_for_jobs(source, cache, False, 0, 1)
    assert not hit
    assert load_document_for_jobs(source, cache, False, 0, 1)[1]
    stat = source.stat()
    source.write_text('# Title\nBravo.')
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    second, hit = load_document_for_jobs(source, cache, False, 0, 1)
    assert not hit
    assert first.chapters[0].text != second.chapters[0].text


def test_single_navigation_anchor_does_not_hide_later_chapters(tmp_path):
    toc = '<ncx><navMap><navPoint><navLabel><text>One</text></navLabel><content src="../a.xhtml#start"/></navPoint></navMap></ncx>'
    doc = make_epub(tmp_path / 'book.epub', {'a.xhtml': '<h1 id="start">One</h1><p>Alpha.</p><h1>Two</h1><p>Beta.</p>'}, toc)
    assert [c.title for c in doc.chapters] == ['One', 'Two']


@pytest.mark.parametrize('prefix', ['', '<p>Alpha.</p>'])
def test_empty_chapter_anchor_survives_resource_boundary(tmp_path, prefix):
    toc = '<ncx><navMap><navPoint><navLabel><text>Next</text></navLabel><content src="../a.xhtml#next"/></navPoint></navMap></ncx>'
    doc = make_epub(tmp_path / 'book.epub', {'a.xhtml': prefix + '<a id="next"/>', 'b.xhtml': '<p>Beta.</p>'}, toc)
    assert canonical(doc)[-1] == ('Next', 'Beta.', None)
    assert len(doc.chapters) == (2 if prefix else 1)


@pytest.mark.parametrize('stop_at', ['pickup', 'bootstrap', 'running'])
def test_shutdown_prevents_spawn_and_retry(tmp_path, monkeypatch, stop_at):
    args = _base_args(tmp_path / 'out')
    args._scheduler_stop = threading.Event()
    args.max_retries = 3
    worker = WorkerConfig('gpu-1', 'CUDAExecutionProvider')
    job = ChapterJob(tmp_path / 'book.md', 1, 'Intro', 'book', '01-Intro', 100, 1)
    pending, spawned = [job], []
    counters = dict(active=0, done=0, failed=0, completed_chunks=0)
    statuses = {worker.name: WorkerStatus()}
    monkeypatch.setattr(sr, 'resolve_job_max_chars', lambda *a: 900)
    monkeypatch.setattr(sr, 'build_worker_command', lambda **kw: ['dummy'])
    monkeypatch.setattr(sr, 'append_runner_log', lambda *a: None)

    class BootstrapLock:
        def acquire(self):
            if stop_at == 'bootstrap':
                args._scheduler_stop.set()
        def release(self):
            pass

    class Process:
        stdout = io.StringIO('')
        returncode = None
        pid = 999
        def poll(self):
            return self.returncode
        def wait(self, timeout=None):
            args._scheduler_stop.set()
            self.returncode = 130
            return 130

    def spawn(*a, **kw):
        spawned.append(Process())
        return spawned[-1]

    monkeypatch.setattr(sr.subprocess, 'Popen', spawn)
    if stop_at == 'pickup':
        args._scheduler_stop.set()
    sr.run_worker(worker, pending, args, tmp_path / 'runner.jsonl', sys.executable,
                  tmp_path / 'absent.py', 1, 1, statuses, counters, threading.Condition(),
                  0, {worker.name: tmp_path / 'worker'}, {}, BootstrapLock())
    assert len(spawned) == (1 if stop_at == 'running' else 0)
    assert pending == [job]
    assert counters == dict(active=0, done=0, failed=0, completed_chunks=0)
    assert not statuses[worker.name].active
