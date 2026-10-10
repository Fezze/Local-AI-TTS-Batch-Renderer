import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from local_tts_renderer.worker_client import PersistentClient
from local_tts_renderer.scheduler_types import ChapterJob
from local_tts_renderer.scheduler_process import _ACTIVE_PROCESSES, terminate_all_active_processes

FAKE_SERVER = '''
import json, os, sys
for raw in sys.stdin:
    command = json.loads(raw)
    if command['command']=='shutdown': break
    base = dict(job_id=command['job_id'], attempt=command['attempt'])
    if command['snapshot']=='crash':
        print('CUDA_CALL: native failure', file=sys.stderr, flush=True)
        os._exit(7)
    if command['snapshot']=='invalid':
        print('not json', flush=True)
        continue
    print(json.dumps(dict(base, event='ready', pid=os.getpid())), flush=True)
    print(json.dumps(dict(base, event='progress', line='[run:render] start')), flush=True)
    print(json.dumps(dict(base, event='result', returncode=0)), flush=True)
'''


@pytest.fixture
def client(monkeypatch):
    popen = subprocess.Popen
    monkeypatch.setattr(subprocess, 'Popen', lambda command, **kw: popen([sys.executable, '-u', '-c', FAKE_SERVER], **kw))
    client = PersistentClient('test-worker')
    yield client
    client.close()
    assert 'test-worker' not in _ACTIVE_PROCESSES


def submit(client, title='one', snapshot='ok', attempt=1):
    job = ChapterJob(Path('book.md'), 1, title, 'book', title, 100, 1,
                     attempt=attempt, document_snapshot=snapshot)
    return client.submit([sys.executable, '-u', '-m', 'local_tts_renderer.cli', '--input', 'book.md'], job,
                         stdout=subprocess.PIPE, text=True, encoding='utf-8',
                         start_new_session=os.name != 'nt')


def test_transport_reuses_process_and_finishes_each_stream(client):
    first = submit(client)
    assert first.wait(5) == 0
    lines = list(first.stdout)
    assert any('worker-ready' in line for line in lines)
    assert any('render' in line for line in lines)
    assert _ACTIVE_PROCESSES['test-worker'].pid == first.pid
    second = submit(client, title='two')
    assert second.pid == first.pid
    assert second.wait(5) == 0
    assert all('"job_id": "book/one"' not in line for line in second.stdout)
    assert first is not second


@pytest.mark.parametrize('failure', ['crash', 'invalid'])
def test_transport_restarts_dead_or_broken_worker(client, failure):
    first = submit(client, snapshot=failure)
    assert first.wait(5) != 0
    if failure == 'crash':
        assert any('CUDA_CALL' in line for line in first.stdout)
    client.close()
    second = submit(client, attempt=2)
    assert second.wait(5) == 0
    assert second.pid != first.pid


def test_idle_process_is_registered_and_terminated(client):
    task = submit(client)
    assert task.wait(5) == 0
    assert client.alive
    terminate_all_active_processes(force=True)
    client.process.wait(5)
    assert not client.alive


def test_persistent_runtime_timeout_retries_with_new_session(tmp_path, monkeypatch):
    from local_tts_renderer import scheduler_runtime as sr
    from local_tts_renderer.scheduler_types import WorkerConfig, WorkerStatus
    from test_scheduler_runtime_flow import _base_args

    script = FAKE_SERVER.replace("    print(json.dumps(dict(base, event='result', returncode=0)), flush=True)", '''    if command['attempt']==1:
        import time
        print(json.dumps(dict(base, event='progress', line='[run:bootstrap] creating kokoro session...')), flush=True)
        while True:
            print(json.dumps(dict(base, event='progress', line='{"heartbeat":true,"completed_chunks":0}')), flush=True)
            time.sleep(.005)
    print(json.dumps(dict(base, event='result', returncode=0)), flush=True)''')
    popen = subprocess.Popen
    spawned = []
    def spawn(command, **kw):
        process = popen([sys.executable, '-u', '-c', script], **kw)
        spawned.append(process)
        return process
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    args = _base_args(tmp_path/'out')
    args.worker_mode = 'persistent'
    args.worker_progress_timeout_seconds = .05
    args.max_retries = 1
    worker = WorkerConfig('cpu-1', 'CPUExecutionProvider')
    job = ChapterJob(tmp_path/'book.md', 1, 'Intro', 'book', 'Intro', 100, 1, document_snapshot='ok')
    monkeypatch.setattr(sr, 'resolve_job_max_chars', lambda *a: 900)
    monkeypatch.setattr(sr, 'build_worker_command', lambda **kw: [sys.executable, '-u', '-m', 'dummy'])
    logs = []
    monkeypatch.setattr(sr, 'append_runner_log', lambda _, payload: logs.append(payload))
    counters = dict(active=0, done=0, failed=0, completed_chunks=0)
    sr.run_worker(worker, [job], args, tmp_path/'runner.jsonl', sys.executable, tmp_path/'absent',
                  1, 1, {worker.name: WorkerStatus()}, counters, threading.Condition(), 0,
                  {worker.name: tmp_path/'worker'}, {}, threading.Lock())
    assert counters['done'] == 1 and counters['failed'] == 0
    assert len(spawned) == 2
    assert all(p.poll() is not None for p in spawned)
    assert [e['reason'] for e in logs if e['event']=='timeout'] == ['no_progress']
    assert [e['phase'] for e in logs if e['event']=='timeout'] == ['chapter_load']


@pytest.mark.skipif(os.name == 'nt', reason='POSIX graceful SIGTERM regression')
def test_active_server_sigterm_reports_interrupt_and_exits(tmp_path, monkeypatch):
    from local_tts_renderer.sources import load_source
    from local_tts_renderer.worker_snapshot import save_snapshot
    from local_tts_renderer.scheduler_process import terminate_process_tree

    source = tmp_path/'book.md'
    source.write_text('# Intro\nA complete sentence.')
    snapshot = save_snapshot(tmp_path, load_source(source))
    script = '''
import time
from types import SimpleNamespace
from local_tts_renderer import cli_entry, cli_render_flow, worker_server
cli_entry.initialize_session = lambda *a: (SimpleNamespace(sess=SimpleNamespace(get_providers=lambda: ['CPUExecutionProvider'])), {})
def render(*a, **kw):
    print('[run:render] start', flush=True)
    while not cli_render_flow.TERMINATION_REQUESTED.is_set():
        time.sleep(.01)
    raise KeyboardInterrupt
cli_entry.main = render
worker_server.main()
'''
    popen = subprocess.Popen
    monkeypatch.setattr(subprocess, 'Popen', lambda command, **kw: popen([sys.executable, '-u', '-c', script], **kw))
    client = PersistentClient('graceful-test')
    env = os.environ | {'PYTHONPATH': str(Path(__file__).resolve().parents[1]/'src')}
    job = ChapterJob(source, 1, 'Intro', 'book', 'Intro', 100, 1, document_snapshot=str(snapshot))
    try:
        task = client.submit([sys.executable, '-u', '-m', 'unused', '--input', str(source), '--out', str(tmp_path/'out')],
                             job, stdout=subprocess.PIPE, text=True, env=env, start_new_session=True)
        while True:
            line = task.lines.get(timeout=10)
            assert line is not None
            if '[run:render] start' in line:
                break
        terminate_process_tree(client.process, force=False)
        assert task.wait(10) == 130
        client.process.wait(10)
    finally:
        client.close()
    assert 'graceful-test' not in _ACTIVE_PROCESSES
