import json
from pathlib import Path
import re
import sys

import numpy as np
import pytest

from local_tts_renderer import cli_render_flow, scheduler_args
from local_tts_renderer.scheduler_jobs import build_jobs
from test_segment_render import execute, runtime  # noqa: F401 -- shared synthetic runtime fixture
from test_epub_logical_chapters import make_epub


@pytest.mark.parametrize('actual_duration_factor', [1, 2])
def test_auto_tasks_render_in_any_order_with_duration_cap_and_resume_skip(
        tmp_path, monkeypatch, runtime, actual_duration_factor):
    source = tmp_path / 'book.epub'
    document = make_epub(source, {'chapter.xhtml': '<h1>Chapter</h1>' +
                         '<p>One complete sentence with six words.</p>' * 40})
    monkeypatch.setattr(sys, 'argv', ['batch', '--input', str(source), '--out', str(tmp_path / 'out'),
                                     '--max-part-minutes', '0.2', '--speed', '1', '--silence-ms', '0',
                                     '--max-chars', '40', '--max-phoneme-chars', '40',
                                     '--warmup-text', '', '--heartbeat-seconds', '0'])
    args = scheduler_args.parse_args()
    chunk_durations = []

    def audio(**kwargs):
        words = len(re.findall(r"\w+(?:['’-]\w+)*", kwargs['text']))
        seconds = words * (60 / 190) * actual_duration_factor
        chunk_durations.append(seconds)
        return [np.zeros(round(seconds * 24000), dtype=np.float32)], 24000

    monkeypatch.setattr(cli_render_flow, 'CREATE_AUDIO_WITH_RETRY', audio)
    options = dict(max_part_minutes=args.max_part_minutes, speed=args.speed,
                   silence_ms=args.silence_ms, max_chars=40, max_phoneme_chars=40)
    jobs, skipped, caches = build_jobs([source], Path(args.output_dir), False, **options)
    assert not skipped and len(jobs) > 2
    assert [job.output_name for job in jobs] == [f'01-{i:02d} - Chapter' for i in range(1, len(jobs) + 1)]
    for job in reversed(jobs):
        assert execute(job, source, args, caches[source], monkeypatch) == 0
    texts, files, durations, part_counts = [], [], [], []
    cap = args.max_part_minutes * 60 * 1.1
    for job in jobs:
        manifest = json.loads((Path(args.output_dir) / job.output_subdir / (job.output_name + '.json')).read_text())
        texts.extend(chunk['text'] for chunk in manifest['chunks'])
        files.extend(part['mp3_path'] for part in manifest['parts'])
        durations.extend(part['duration_seconds'] for part in manifest['parts'])
        part_counts.append(len(manifest['parts']))
        assert manifest['max_part_minutes'] == pytest.approx(args.max_part_minutes * 1.1)
        for part in manifest['parts'][:-1]:
            assert part['duration_seconds'] >= cap
    assert ' '.join(' '.join(texts).split()) == ' '.join(document.chapters[0].text.split())
    assert len(set(files)) == len(files)
    assert not any('segment' in Path(file).name for file in files)
    if actual_duration_factor == 1:
        assert [Path(file).stem for file in files] == [job.output_name for job in jobs]
    else:
        assert [Path(file).name for file in files] == sorted(Path(file).name for file in files)
    assert all(Path(file).stat().st_size > 0 for file in files)
    assert all(duration <= cap + max(chunk_durations) for duration in durations)
    if actual_duration_factor == 1:
        assert all(count == 1 for count in part_counts)
    else:
        assert any(count > 1 for count in part_counts)
    remaining, skipped, _ = build_jobs([source], Path(args.output_dir), False, **options)
    assert remaining == [] and len(skipped) == len(jobs)


def test_auto_segment_interruption_resumes_without_touching_completed_sibling(tmp_path, monkeypatch, runtime):
    from test_segment_render import source_and_args

    source, document, args = source_and_args(tmp_path, monkeypatch)
    args.job_max_chars = None
    args.max_part_minutes = 0.15
    output = Path(args.output_dir)
    options = dict(max_part_minutes=args.max_part_minutes, speed=args.speed,
                   silence_ms=args.silence_ms, max_chars=40, max_phoneme_chars=40)
    jobs, _, caches = build_jobs([source], output, False, **options)
    assert len(jobs) > 2
    assert execute(jobs[1], source, args, caches[source], monkeypatch) == 0
    sibling = output / jobs[1].output_subdir / (jobs[1].output_name + '.json')
    sibling_before = sibling.read_bytes()
    write_audio = cli_render_flow.OutputPartWriter.write_audio

    def interrupt(self, audio):
        write_audio(self, audio)
        cli_render_flow.request_termination()

    monkeypatch.setattr(cli_render_flow.OutputPartWriter, 'write_audio', interrupt)
    before = len(runtime)
    with pytest.raises(KeyboardInterrupt):
        execute(jobs[0], source, args, caches[source], monkeypatch)
    pending, skipped, _ = build_jobs([source], output, False, **options)
    resumed = next(job for job in pending if job.segment_index == 1)
    assert len(skipped) == 1
    assert (resumed.text_start, resumed.text_end, resumed.output_part_minutes) == (
        jobs[0].text_start, jobs[0].text_end, jobs[0].output_part_minutes)
    monkeypatch.setattr(cli_render_flow.OutputPartWriter, 'write_audio', write_audio)
    assert execute(resumed, source, args, caches[source], monkeypatch) == 0
    expected = document.chapters[0].text[resumed.text_start:resumed.text_end]
    assert ' '.join(' '.join(runtime[before:]).split()) == ' '.join(expected.split())
    assert sibling.read_bytes() == sibling_before
