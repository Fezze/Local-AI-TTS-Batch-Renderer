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
    metadata = subprocess.check_output([str(python), '-c', 'from importlib.metadata import requires; print("\\n".join(requires("local-tts-renderer")))'], cwd=tmp_path, env=env, text=True)
    assert 'kokoro-onnx==0.4.7' in metadata
    assert 'onnxruntime-gpu==1.24.4' in metadata
