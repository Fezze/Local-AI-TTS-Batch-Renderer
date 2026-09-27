import json
import os
from pathlib import Path
import shutil
import site
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('editable', [False, True])
def test_installed_commands_and_worker_without_repository_scripts(tmp_path, editable):
    # Build offline. A system interpreter may supply build tooling absent from the dev venv.
    candidates = list(dict.fromkeys(filter(None, [sys.executable, shutil.which('python')])))
    builder = next((candidate for candidate in candidates if subprocess.run(
        [candidate, '-c', 'import setuptools, wheel, pip'], capture_output=True).returncode == 0), None)
    if builder is None:
        pytest.fail('Install setuptools, wheel and pip to run packaging smoke tests')
    project = tmp_path / 'package'
    shutil.copytree(ROOT / 'src', project / 'src', ignore=shutil.ignore_patterns('__pycache__', '*.egg-info'))
    for name in ['pyproject.toml', 'requirements.txt']:
        shutil.copy(ROOT / name, project / name)
    wheels = tmp_path / 'wheels'
    wheels.mkdir()
    method = 'build_editable' if editable else 'build_wheel'
    subprocess.run([builder, '-c', f'from setuptools.build_meta import {method}; {method}({str(wheels)!r})'], cwd=project, check=True, capture_output=True)
    wheel = next(wheels.glob('*.whl'))
    venv = tmp_path / 'installed'
    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(venv)], check=True)
    python = venv / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    subprocess.run([builder, '-m', 'pip', '--python', str(python), 'install', '--no-index', '--no-deps', str(wheel)], check=True, capture_output=True)
    # Reuse already installed runtime dependencies; no downloads or real GPU inference.
    env = {key: value for key, value in os.environ.items() if key != 'PYTHONPATH'}
    target_site = subprocess.check_output([str(python), '-c', 'import site; print(site.getsitepackages()[-1])'], text=True).strip()
    Path(target_site, 'test-runtime-deps.pth').write_text('\n'.join(site.getsitepackages()) + '\n')
    for command in ['local-tts-render', 'local-tts-batch']:
        executable = venv / ('Scripts' if os.name == 'nt' else 'bin') / (command + ('.exe' if os.name == 'nt' else ''))
        result = subprocess.run([str(executable), '--help'], cwd=tmp_path, env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert '--input' in result.stdout
    script = '''import json, sys
from pathlib import Path
from local_tts_renderer.scheduler_args import parse_args
from local_tts_renderer.scheduler_jobs import build_worker_command
from local_tts_renderer.scheduler_types import ChapterJob
sys.argv = ['batch', '--input', 'book.md']
args = parse_args()
job = ChapterJob(Path('book.md'), 1, 'Intro', 'book', '01-Intro', 10, 1)
command = build_worker_command(Path(sys.executable), Path('missing/md_to_audio.py'), args, job.source_path, job, 100, None)
print(json.dumps(command + ['--list-chapters']))
'''
    command = json.loads(subprocess.check_output([str(python), '-c', script], cwd=tmp_path, env=env, text=True))
    assert command[1:4] == ['-u', '-m', 'local_tts_renderer.cli']
    (tmp_path / 'book.md').write_text('# Intro\nA test paragraph.\n')
    result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'Intro' in result.stdout
    result = subprocess.run([str(python), '-m', 'local_tts_renderer.worker_server'],
                            input='{"command":"shutdown"}\n', cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ''
    # Exercise two protocol tasks in the installed package with a synthetic session.
    protocol_script = """import sys
from pathlib import Path
from types import SimpleNamespace
from local_tts_renderer import cli_entry, worker_server
cli_entry.initialize_session = lambda *a: (SimpleNamespace(sess=SimpleNamespace(get_providers=lambda: ['CPUExecutionProvider'])), {})
cli_entry.main = lambda *a, **kw: 0
worker_server.main()
"""
    snapshot_script = """from pathlib import Path
from local_tts_renderer.sources import load_source
from local_tts_renderer.worker_snapshot import save_snapshot
print(save_snapshot(Path('snapshots'), load_source(Path('book.md').resolve())))
"""
    snapshot = subprocess.check_output([str(python), '-c', snapshot_script], cwd=tmp_path, env=env, text=True).strip()
    payload = ''.join(json.dumps(dict(command='render', job_id=str(i), attempt=1,
                      snapshot=snapshot, argv=['--input', 'book.md', '--out', 'audio'])) + '\n' for i in range(2))
    result = subprocess.run([str(python), '-c', protocol_script], input=payload, cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert [e['returncode'] for e in events if e['event']=='result'] == [0, 0]
    assert [e.get('reused', False) for e in events if e['event']=='ready'] == [False, True]
    metadata = subprocess.check_output([str(python), '-c', 'from importlib.metadata import requires; print("\\n".join(requires("local-tts-renderer")))'], cwd=tmp_path, env=env, text=True)
    assert 'kokoro-onnx==0.4.7' in metadata
    assert 'onnxruntime-gpu==1.24.4' in metadata
