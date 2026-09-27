import json
from pathlib import Path

import pytest

from local_tts_renderer.cli_render_cleanup import _owned_stem
from local_tts_renderer.part_naming import numbered_part_stem
from local_tts_renderer.scheduler_jobs import build_jobs, reset_job_for_fresh_run
from test_segment_render import execute, runtime, source_and_args  # noqa: F401


@pytest.mark.parametrize('stem,index,expected', [
    ('04-Title', 1, '04-01 - Title'),
    ('04-Title', 2, '04-02 - Title'),
    ('04-01 - Title', 2, '04-01-02 - Title'),
])
def test_standard_part_names(stem, index, expected):
    assert numbered_part_stem(stem, index) == expected


@pytest.mark.parametrize('stem,owned', [
    ('04-01 - Title', True),
    ('04-01-01 - Title', True),
    ('04-01-02 - Title', True),
    ('04-02 - Title', False),
    ('04-02-01 - Title', False),
    ('04-010-01 - Title', False),
])
def test_part_cleanup_owns_only_its_own_numbered_children(stem, owned):
    assert _owned_stem(stem, '04-01 - Title') is owned


def test_fresh_cleanup_preserves_neighboring_numbered_part(tmp_path, monkeypatch, runtime):
    source, _, args = source_and_args(tmp_path, monkeypatch)
    output = Path(args.output_dir)
    jobs, _, caches = build_jobs([source], output, False, job_max_chars=190,
                                max_chars=40, max_phoneme_chars=40)
    for job in jobs[:2]:
        assert execute(job, source, args, caches[source], monkeypatch) == 0
    manifest = output / jobs[1].output_subdir / (jobs[1].output_name + '.json')
    audio = Path(json.loads(manifest.read_text())['parts'][0]['mp3_path'])
    original = {path: path.read_bytes() for path in [manifest, audio]}
    reset_job_for_fresh_run(output, jobs[0])
    for path, contents in original.items():
        assert path.read_bytes() == contents


def test_old_segment_naming_plan_is_rejected_before_rendering(tmp_path):
    source = tmp_path / 'book.md'
    source.write_text('# Chapter\n\n' + 'A complete sentence.\n\n' * 30)
    output = tmp_path / 'out'
    build_jobs([source], output, False, job_max_chars=80)
    plan = next((output / '.cache' / 'work-plans').glob('*.json'))
    payload = json.loads(plan.read_text())
    payload['version'] = 1
    plan.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='saved worker plan'):
        build_jobs([source], output, False, job_max_chars=80)
    assert not list(output.rglob('*.mp3'))
