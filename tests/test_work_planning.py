from dataclasses import replace
from pathlib import Path
import sys

import pytest

from local_tts_renderer.work_planning import natural_text_ranges, split_chapter_job
from local_tts_renderer.sources.model import SourceChapter
from local_tts_renderer.scheduler_jobs import build_jobs, build_worker_command, select_next_job
from local_tts_renderer.scheduler_types import ChapterJob, WorkerConfig, WorkerStatus
from local_tts_renderer import scheduler_args, cli_runtime


def slices(text, target):
    ranges = natural_text_ranges(text, target)
    parts = [text[a:b] for a,b in ranges]
    assert ''.join(parts) == text
    assert all(part.strip() for part in parts)
    assert ranges[0][0] == 0 and ranges[-1][1] == len(text)
    assert all(a[1] == b[0] for a,b in zip(ranges,ranges[1:]))
    return parts


def test_paragraphs_are_preferred_over_sentence_boundaries():
    text = 'First sentence. Second sentence.\n\nThird sentence. Fourth sentence.\n\nLast paragraph.'
    assert slices(text, 50) == ['First sentence. Second sentence.\n\n', 'Third sentence. Fourth sentence.\n\nLast paragraph.']


def test_long_paragraph_splits_at_sentences_and_keeps_quotes():
    text = '“First sentence!” Second sentence. Third sentence.'
    assert slices(text, 25) == ['“First sentence!” ', 'Second sentence. ', 'Third sentence.']


def test_overlong_sentence_exceeds_target_instead_of_cutting():
    text = 'A ' + 'very ' * 50 + 'long sentence. Next sentence.'
    parts = slices(text, 30)
    assert parts[0].endswith('long sentence. ')
    assert len(parts[0]) > 30
    assert parts[1] == 'Next sentence.'


def test_abbreviations_do_not_split_inside_a_sentence():
    text = 'Dr. Smith met Mr. Brown and J. Doe. Next sentence.'
    assert slices(text, 12) == ['Dr. Smith met Mr. Brown and J. Doe. ', 'Next sentence.']


@pytest.mark.parametrize('target', [0, 1000])
def test_disabled_or_small_text_keeps_one_job(target):
    assert natural_text_ranges('Small sentence.', target) == [(0,15)]


def test_split_jobs_are_stable_and_cpu_can_take_bounded_work():
    text = ('First sentence. Second sentence.\n\n' * 10).strip()
    chapter = SourceChapter('Chapter',text)
    base = ChapterJob(Path('book.epub'),3,'Chapter','book','03-Chapter',len(text),30)
    jobs = split_chapter_job(base, chapter, 100, 20)
    assert len(jobs)>3
    assert len({job.output_name for job in jobs}) == len(jobs)
    assert {job.chapter_index for job in jobs} == {3}
    assert ''.join(text[job.text_start:job.text_end] for job in jobs) == text
    assert jobs == split_chapter_job(base,chapter,100,20)
    statuses={'gpu-1':WorkerStatus(active=True),'cpu-1':WorkerStatus()}
    assert select_next_job(jobs, WorkerConfig('cpu-1','CPUExecutionProvider'),statuses,12000,False) is not None


def test_worker_command_propagates_exact_range(monkeypatch):
    monkeypatch.setattr(sys,'argv',['batch','--input','book.md','--job-max-chars','80'])
    args=scheduler_args.parse_args()
    job=ChapterJob(Path('book.md'),2,'Chapter','book','02-Chapter-segment-0002',40,1,text_start=40,text_end=80,segment_index=2,segment_count=3)
    cmd=build_worker_command(Path(sys.executable),Path('unused'),args,job.source_path,job,450,Path('cache.json'))
    monkeypatch.setattr(sys,'argv',['render',*cmd[4:]])
    parsed=cli_runtime.parse_args()
    assert (parsed.chapter_index,parsed.chapter_text_start,parsed.chapter_text_end)==(2,40,80)
    assert parsed.output_name.endswith('segment-0002')


def test_changed_task_size_is_rejected_before_mixing_outputs(tmp_path):
    source=tmp_path/'book.md';source.write_text('# Chapter\n\n'+('A test sentence.\n\n'*30))
    output=tmp_path/'out'
    jobs,_,_=build_jobs([source],output,False,job_max_chars=80)
    assert len(jobs)>1
    with pytest.raises(ValueError,match='saved worker plan'):
        build_jobs([source],output,False,job_max_chars=120)
    assert not list(output.rglob('*.mp3'))
    again,_,_=build_jobs([source],output,False,job_max_chars=80)
    assert jobs==again


def test_heading_is_not_left_in_a_task_on_its_own():
    chapter = SourceChapter('Heading', 'Heading\n\nFirst complete sentence. Second complete sentence.')
    job = ChapterJob(Path('book.epub'),1,'Heading','book','01-Heading',len(chapter.text),3)
    tasks = split_chapter_job(job,chapter,30,20)
    assert len(tasks)==2
    assert chapter.text[tasks[0].text_start:tasks[0].text_end]=='Heading\n\nFirst complete sentence. '
    assert ''.join(chapter.text[t.text_start:t.text_end] for t in tasks)==chapter.text


def test_existing_unsplit_checkpoint_keeps_original_task_identity(tmp_path):
    source=tmp_path/'book.md'
    source.write_text('# Chapter\n\n'+('A complete sentence.\n\n'*20))
    out=tmp_path/'out'
    checkpoint=out/'book'/'01-Chapter.resume.json'
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text('{"render_max_chars":777}')
    jobs,skipped,_=build_jobs([source],out,False,job_max_chars=80)
    assert not skipped and len(jobs)==1
    assert jobs[0].text_start==0 and jobs[0].text_end is None
    assert jobs[0].output_name=='01-Chapter'
    assert jobs[0].render_max_chars==777


def test_dots_in_chapter_titles_do_not_collapse_segment_paths():
    chapter=SourceChapter('Mr. Example', 'First sentence.\n\nSecond sentence.\n\nThird sentence.')
    job=ChapterJob(Path('book.epub'),1,chapter.title,'book','01-Mr. Example',len(chapter.text),3)
    tasks=split_chapter_job(job,chapter,20,20)
    manifests=[Path(task.output_name).with_suffix('.json') for task in tasks]
    assert len(set(manifests))==len(tasks)==3
