import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which('pwsh')


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'project with spaces'
    shutil.copytree(ROOT / 'scripts', root / 'scripts')
    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(root / '.venv')], check=True)
    stub = '''import json, os, pathlib, sys
with pathlib.Path('calls.jsonl').open('a') as f:
    f.write(json.dumps([pathlib.Path(sys.argv[0]).name, sys.argv[1:], str(pathlib.Path.cwd())]) + '\\n')
sys.exit(int(os.environ.get('STUB_EXIT', '0')) if pathlib.Path(sys.argv[0]).name == os.environ.get('FAIL_SCRIPT') else 0)
'''
    for name in ['scripts/bootstrap_models.py', 'scripts/doctor.py', 'md_to_audio.py', 'run_tts_batch.py']:
        (root / name).write_text(stub)
    return root


@pytest.mark.parametrize('batch', [False, True])
@pytest.mark.parametrize('help_only', [False, True])
def test_start_from_external_directory(project, tmp_path, batch, help_only):
    suffix = '-batch' if batch else ''
    if os.name == 'nt':
        command = [PWSH or 'pwsh', '-NoProfile', '-File', str(project / 'scripts' / f'start{suffix}.ps1')]
    else:
        command = ['bash', str(project / 'scripts' / f'start{suffix}.sh')]
    arguments = ['--help'] if help_only else ['--input', 'book with spaces.md', '--out', 'audio output', '--model-dir', 'my models', '--providers', 'CPUExecutionProvider']
    result = subprocess.run(command + arguments, cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in (project / 'calls.jsonl').read_text().splitlines()]
    entry = 'run_tts_batch.py' if batch else 'md_to_audio.py'
    assert [call[0] for call in calls] == ([entry] if help_only else ['bootstrap_models.py', 'doctor.py', entry])
    assert all(call[1] == arguments and Path(call[2]) == project for call in calls)


@pytest.mark.skipif(os.name != 'nt' or not PWSH, reason='native Windows PowerShell required')
@pytest.mark.parametrize('batch', [False, True])
@pytest.mark.parametrize('failed', ['bootstrap_models.py', 'doctor.py', 'entry'])
def test_powershell_start_propagates_exit(project, tmp_path, failed, batch):
    if failed == 'entry':
        failed = 'run_tts_batch.py' if batch else 'md_to_audio.py'
    wrapper = 'start-batch.ps1' if batch else 'start.ps1'
    env = {**os.environ, 'FAIL_SCRIPT': failed, 'STUB_EXIT': '7'}
    result = subprocess.run([PWSH, '-NoProfile', '-File', str(project / 'scripts' / wrapper), '--input', 'book.md'], cwd=tmp_path, env=env)
    assert result.returncode == 7
    calls = [json.loads(line)[0] for line in (project / 'calls.jsonl').read_text().splitlines()]
    assert calls[-1] == failed


@pytest.mark.skipif(os.name != 'nt' or not PWSH, reason='native Windows PowerShell required')
@pytest.mark.parametrize('stage', ['--upgrade', 'requirements.txt', 'requirements-dev.txt'])
def test_powershell_setup_stops_at_failed_pip(project, tmp_path, stage):
    (project / 'pip.py').write_text('''import os, pathlib, sys
with pathlib.Path('pip-calls.txt').open('a') as f:
    f.write(' '.join(sys.argv[1:]) + '\\n')
sys.exit(9 if os.environ['FAIL_STAGE'] in sys.argv else 0)
''')
    result = subprocess.run([PWSH, '-NoProfile', '-File', str(project / 'scripts/setup.ps1'), '-Python', sys.executable, '-Dev'], cwd=tmp_path, env={**os.environ, 'FAIL_STAGE': stage})
    assert result.returncode == 9
    assert stage in (project / 'pip-calls.txt').read_text().splitlines()[-1]


@pytest.mark.skipif(os.name != 'nt' or not PWSH, reason='native Windows PowerShell required')
def test_powershell_recreates_broken_venv(project, tmp_path):
    shutil.rmtree(project / '.venv')
    (project / '.venv').mkdir()
    (project / '.venv' / 'broken-marker').touch()
    (project / 'pip.py').write_text('raise SystemExit(0)\n')
    result = subprocess.run([PWSH, '-NoProfile', '-File', str(project / 'scripts/setup.ps1'), '-Python', sys.executable], cwd=tmp_path)
    assert result.returncode == 0
    assert not (project / '.venv' / 'broken-marker').exists()
    subprocess.run([str(project / '.venv/Scripts/python.exe'), '-V'], check=True)


def test_doctor_output_alias(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('review_doctor', ROOT / 'scripts/doctor.py')
    doctor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(doctor)
    observed = []
    monkeypatch.setattr(doctor, 'check_paths', lambda output, model: (observed.append(output) or True, []))
    monkeypatch.setattr(doctor, 'check_models', lambda *_: (True, []))
    monkeypatch.setattr(doctor, 'check_onnx', lambda *_: (True, []))
    monkeypatch.setattr(doctor, 'check_temp_dir', lambda: (True, ''))
    monkeypatch.setattr(doctor, 'check_py_compile', lambda *_: (True, []))
    assert doctor.main(['--out', str(tmp_path / 'audio')]) == 0
    assert observed == [tmp_path / 'audio']


@pytest.mark.skipif(os.name != 'nt' or not PWSH, reason='native Windows PowerShell required')
def test_powershell_setup_stops_at_failed_venv_creation(project, tmp_path):
    shutil.rmtree(project / '.venv')
    failing_python = tmp_path / 'fail-python.cmd'
    failing_python.write_text('@exit /b 8\n')
    result = subprocess.run([PWSH, '-NoProfile', '-File', str(project / 'scripts/setup.ps1'),
                             '-Python', str(failing_python)], cwd=tmp_path)
    assert result.returncode == 8
    assert not (project / '.venv').exists()
