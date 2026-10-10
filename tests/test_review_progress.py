import io
import json
import sys
import threading

import pytest

from local_tts_renderer import scheduler_runtime as sr, scheduler_args
from local_tts_renderer.scheduler_progress import ProgressWatchdog
from local_tts_renderer.scheduler_types import ChapterJob, WorkerConfig, WorkerStatus
from test_scheduler_runtime_flow import _base_args, _DummyThread


def test_watchdog_progress_and_disabled():
    watchdog = ProgressWatchdog(10)
    watchdog.observe('[run:warmup] start', 0)
    assert watchdog.stalled_seconds(100) is None
    watchdog.observe('[run:render] start', 100)
    watchdog.observe('{"heartbeat":true,"completed_chunks":0}', 109)
    assert watchdog.stalled_seconds(110) == 10
    watchdog.observe('[1/3] 33.3% chapter=1 chunk=1 eta=2.0s', 111)
    assert watchdog.stalled_seconds(120) is None
    watchdog.observe('{"heartbeat":true,"completed_chunks":1}', 120)
    assert watchdog.stalled_seconds(121) == 10
    watchdog.observe('{"heartbeat":true,"completed_chunks":2}', 122)
    assert watchdog.stalled_seconds(125) is None
    watchdog.timeout = 0
    assert watchdog.stalled_seconds(1000) is None


@pytest.mark.parametrize('value', ['-1', 'nan', 'inf'])
def test_invalid_progress_timeout(monkeypatch, value):
    monkeypatch.setattr(sys, 'argv', ['batch', '--input', 'book.md', '--worker-progress-timeout-seconds', value])
    with pytest.raises(SystemExit) as error:
        scheduler_args.parse_args()
    assert error.value.code == 2


@pytest.mark.parametrize('timeout', [0, 3])
def test_busy_worker_progress_timeout_and_retry(tmp_path, monkeypatch, timeout):
    args = _base_args(tmp_path / 'out')
    args.worker_progress_timeout_seconds = timeout
    args.max_retries = 1
    source = tmp_path / 'book.md'
    job = ChapterJob(source, 1, 'Intro', 'book', '01-Intro', 100, 1)
    worker = WorkerConfig('cpu-1', 'CPUExecutionProvider')
    logs, killed, spawned = [], [], []
    ticks = iter(range(1000))
    monkeypatch.setattr(sr.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(sr, 'resolve_job_max_chars', lambda *a: 900)
    monkeypatch.setattr(sr, 'build_worker_command', lambda **kw: ['dummy'])
    monkeypatch.setattr(sr, 'append_runner_log', lambda _, payload: logs.append(payload))
    monkeypatch.setattr(sr, 'terminate_process_tree', lambda process, **kw: killed.append(process))

    class Process:
        stdout = io.StringIO('')
        pid = 999
        returncode = None
        def poll(self):
            return self.returncode
        def wait(self, timeout=None):
            self.returncode = -9 if self in killed else 0
            return self.returncode

    def popen(*a, **kw):
        process = Process()
        spawned.append(process)
        assert 'PYTHONPATH' in kw['env']
        return process

    def reader(stream, queue):
        queue.put('[run:render] start\n')
        for _ in range(20):
            queue.put(json.dumps({'heartbeat': True, 'completed_chunks': 0, 'total_chunks': 1}) + '\n')
        queue.put(None)
        return _DummyThread()

    monkeypatch.setattr(sr.subprocess, 'Popen', popen)
    monkeypatch.setattr(sr, 'start_stdout_reader', reader)
    counters = dict(active=0, done=0, failed=0, completed_chunks=0)
    sr.run_worker(worker, [job], args, tmp_path / 'runner.jsonl', sys.executable,
                  tmp_path / 'absent.py', 1, 1, {worker.name: WorkerStatus()}, counters,
                  threading.Condition(), 0, {worker.name: tmp_path / 'worker'}, {}, threading.Lock())
    if timeout:
        assert len(spawned) == len(killed) == 2
        assert counters['failed'] == 1
        assert [entry['reason'] for entry in logs if entry.get('event') == 'timeout'] == ['no_progress'] * 2
    else:
        assert len(spawned) == 1
        assert not killed
        assert counters['done'] == 1
