import io
import json
from pathlib import Path
import sys

import pytest

from local_tts_renderer import cli_entry, cli_render_flow, worker_server
from local_tts_renderer.scheduler_jobs import build_jobs, build_worker_command
from local_tts_renderer.worker_snapshot import SnapshotReader, save_snapshot
from test_segment_render import runtime, source_and_args
from test_epub_logical_chapters import make_epub


def commands_for(jobs, args, caches):
    return [json.dumps(dict(command='render', job_id=j.output_name, attempt=j.attempt,
            snapshot=j.document_snapshot,
            argv=build_worker_command(Path(sys.executable), Path('unused'), args, j.source_path,
                                      j, 40, caches[j.source_path])[4:])) + '\n' for j in jobs]


def test_one_session_multiple_segments_and_document_switch(tmp_path, monkeypatch, runtime):
    source, doc, args = source_and_args(tmp_path, monkeypatch)
    jobs, _, caches = build_jobs([source], Path(args.output_dir), False, job_max_chars=190, max_chars=40, max_phoneme_chars=40)
    other = tmp_path/'other.epub'
    other_doc = make_epub(other, {'a.xhtml': '<h1>Other</h1><p>A different book.</p>'})
    other_jobs, _, other_caches = build_jobs([other], Path(args.output_dir), False, max_chars=40, max_phoneme_chars=40)
    chosen = jobs[:2] + other_jobs
    calls = []
    original = cli_entry.initialize_session
    def initialize(*a):
        calls.append(1)
        return original(*a)
    monkeypatch.setattr(cli_entry, 'initialize_session', initialize)
    monkeypatch.setattr(cli_entry, '_load_document_for_source', lambda *a: pytest.fail('EPUB reparsed'))
    output = io.StringIO()
    worker_server.serve(commands_for(chosen, args, caches | other_caches), output)
    events = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(calls) == 1
    assert [e['returncode'] for e in events if e['event']=='result'] == [0, 0, 0]
    assert [e.get('reused', False) for e in events if e['event']=='ready'] == [False, True, True]
    for job in chosen:
        manifest = json.loads((Path(args.output_dir)/job.output_subdir/(job.output_name+'.json')).read_text())
        assert manifest['chunks'][0]['index'] == 1
    assert 'A different book.' in ' '.join(runtime)


def test_persistent_interruption_then_new_session_resumes(tmp_path, monkeypatch, runtime):
    source, doc, args = source_and_args(tmp_path, monkeypatch)
    jobs, _, caches = build_jobs([source], Path(args.output_dir), False, job_max_chars=190, max_chars=40, max_phoneme_chars=40)
    original = cli_render_flow.OutputPartWriter.write_audio
    def interrupted(self, audio):
        original(self, audio)
        cli_render_flow.request_termination()
    monkeypatch.setattr(cli_render_flow.OutputPartWriter, 'write_audio', interrupted)
    output = io.StringIO()
    worker_server.serve(commands_for(jobs[:2], args, caches), output)
    events = [json.loads(line) for line in output.getvalue().splitlines()]
    assert events[-1]['event'] == 'error' and events[-1]['returncode'] == 130
    assert len([e for e in events if e['event']=='ready']) == 1
    monkeypatch.setattr(cli_render_flow.OutputPartWriter, 'write_audio', original)
    output = io.StringIO()
    worker_server.serve(commands_for(jobs[:2], args, caches), output)
    assert [json.loads(line)['returncode'] for line in output.getvalue().splitlines() if json.loads(line)['event']=='result'] == [0, 0]
    text = doc.chapters[0].text[:jobs[1].text_end]
    assert ' '.join(' '.join(runtime).split()) == ' '.join(text.split())


def test_snapshot_integrity_identity_and_last_document_only(tmp_path):
    first = make_epub(tmp_path/'one.epub', {'a.xhtml':'<p>One.</p>'})
    second = make_epub(tmp_path/'two.epub', {'a.xhtml':'<p>Two.</p>'})
    a, b = save_snapshot(tmp_path, first), save_snapshot(tmp_path, second)
    reader = SnapshotReader()
    loaded = reader.load(a, first.path)
    assert reader.load(a, first.path) is loaded
    assert reader.load(b, second.path).chapters[0].text == 'Two.'
    assert reader.path == b
    with pytest.raises(ValueError, match='another source'):
        reader.load(b, first.path)
    a.write_text(a.read_text().replace('One.', 'Modified.'))
    with pytest.raises(ValueError, match='identity'):
        reader.load(a, first.path)


def test_partial_tasks_continue_in_same_healthy_session(tmp_path, monkeypatch, runtime):
    source, doc, args = source_and_args(tmp_path, monkeypatch)
    args.max_parts_per_run = 1
    args.max_part_minutes = .001
    jobs, _, caches = build_jobs([source], Path(args.output_dir), False, job_max_chars=190, max_chars=40, max_phoneme_chars=40)
    output = io.StringIO()
    manifest = Path(args.output_dir)/jobs[0].output_subdir/(jobs[0].output_name+'.json')
    def pending_commands():
        for command in commands_for([jobs[0]] * 12, args, caches):
            if manifest.exists():
                break
            yield command
    worker_server.serve(pending_commands(), output)
    events = [json.loads(line) for line in output.getvalue().splitlines()]
    assert not [e for e in events if e['event'] == 'error'], output.getvalue()
    codes = [e['returncode'] for e in events if e['event']=='result']
    assert codes[0] == 75 and codes[-1] == 0
    assert set(codes) == {0, 75}
    assert len([e for e in events if e['event']=='ready' and not e.get('reused')]) == 1
    text = doc.chapters[0].text[jobs[0].text_start:jobs[0].text_end]
    assert ' '.join(' '.join(runtime).split()) == ' '.join(text.split())


@pytest.mark.parametrize('stem, owned', [
    ('01-02 - Chapter-segment-0001', True),
    ('01-01 - Chapter - segment - 0001', True),
    ('01-02 - Chapter-segment-00010', False),
    ('02-02 - Chapter-segment-0001', False),
    ('01-02 - Other-segment-0001', False),
])
def test_numbered_part_ownership_stays_scoped(stem, owned):
    from local_tts_renderer.cli_render_cleanup import _owned_stem
    assert _owned_stem(stem, '01-Chapter-segment-0001') is owned
