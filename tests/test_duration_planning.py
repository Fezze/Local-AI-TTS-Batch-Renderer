from pathlib import Path
import sys

import pytest

from local_tts_renderer.duration_planning import duration_plan_settings, duration_text_ranges
from local_tts_renderer.scheduler_jobs import build_jobs
from local_tts_renderer import scheduler_args


def settings(minutes=30, speed=1, silence_ms=0, chunk_chars=450):
    return duration_plan_settings(minutes, speed, silence_ms, chunk_chars)


def test_estimated_parts_are_near_thirty_minutes_and_preserve_all_text():
    text = ('A complete sentence with five words.\n\n' * 2400)
    ranges = duration_text_ranges(text, settings())
    assert ''.join(text[start:end] for start, end in ranges) == text
    assert len(ranges) == 3
    for start, end in ranges[:-1]:
        minutes = len(text[start:end].split()) / 190
        assert 27 <= minutes <= 33
        assert text[start:end].endswith('\n\n')


def test_speed_pause_and_word_density_change_segment_size():
    text = 'One two three four five six.\n\n' * 4000
    fast = duration_text_ranges(text, settings(speed=1.2))
    slow = duration_text_ranges(text, settings(speed=0.6))
    pauses = duration_text_ranges(text, settings(speed=1.2, silence_ms=2000, chunk_chars=40))
    assert slow[0][1] < fast[0][1]
    assert pauses[0][1] < fast[0][1]
    long_words = text.replace('One', 'Extraordinarily').replace('two', 'complicated')
    longer = duration_text_ranges(long_words, settings(speed=1.2))
    assert longer[0][1] > fast[0][1]
    assert len(long_words[:longer[0][1]].split()) == len(text[:fast[0][1]].split())


def test_oversized_paragraph_uses_sentences_instead_of_short_paragraph():
    short = 'Short paragraph.\n\n'
    text = short + 'Another complete sentence follows. ' * 2400
    ranges = duration_text_ranges(text, settings())
    assert ranges[0][1] > len(short)
    assert 27 <= len(text[:ranges[0][1]].split()) / 190 <= 33
    assert ''.join(text[start:end] for start, end in ranges) == text


def test_overlong_sentence_and_short_final_range_are_not_lost():
    sentence = 'Very ' * 8000 + 'long sentence. '
    text = sentence + 'Last short sentence.\n\n'
    ranges = duration_text_ranges(text, settings())
    assert ranges == [(0, len(sentence)), (len(sentence), len(text))]
    assert duration_text_ranges('Short chapter.', settings()) == [(0, 14)]


@pytest.mark.parametrize('minutes,speed', [(0, 1), (float('nan'), 1), (30, 0), (30, float('inf'))])
def test_invalid_duration_parameters_are_rejected(minutes, speed):
    with pytest.raises(ValueError):
        settings(minutes=minutes, speed=speed)


@pytest.mark.parametrize('value,expected', [('auto', None), ('0', 0), ('12000', 12000)])
def test_cli_accepts_auto_disabled_and_explicit_character_modes(monkeypatch, value, expected):
    monkeypatch.setattr(sys, 'argv', ['batch', '--input', 'book.md', '--job-max-chars', value])
    assert scheduler_args.parse_args().job_max_chars == expected


@pytest.mark.parametrize('changed', [dict(speed=1.2), dict(max_part_minutes=20), dict(silence_ms=500), dict(max_chars=300)])
def test_resume_requires_same_duration_plan(tmp_path, changed):
    source = tmp_path / 'book.md'
    source.write_text('# Chapter\n\n' + 'A complete sentence.\n\n' * 3000)
    output = tmp_path / 'out'
    jobs, _, _ = build_jobs([source], output, False)
    same, _, _ = build_jobs([source], output, False)
    assert same == jobs
    with pytest.raises(ValueError, match='saved worker plan'):
        build_jobs([source], output, False, **changed)


def test_explicit_zero_retains_unsplit_thirty_minute_limit(tmp_path):
    source = tmp_path / 'book.md'
    source.write_text('# Chapter\n\n' + 'A complete sentence.\n\n' * 3000)
    jobs, _, _ = build_jobs([source], tmp_path / 'out', False, job_max_chars=0)
    assert len(jobs) == 1
    assert jobs[0].text_end is None and jobs[0].output_part_minutes is None


def test_process_pool_propagates_duration_settings(tmp_path):
    sources = [tmp_path / f'book-{i}.md' for i in range(2)]
    for source in sources:
        source.write_text('# Chapter\n\n' + 'A complete sentence.\n\n' * 400)
    jobs, _, _ = build_jobs(sources, tmp_path / 'out', False,
                            max_part_minutes=2, speed=0.8, silence_ms=500)
    assert len(jobs) > 4
    assert {job.source_path for job in jobs} == set(sources)
    assert all(job.output_part_minutes == pytest.approx(2.2) for job in jobs)
    same, _, _ = build_jobs(sources, tmp_path / 'out', False,
                            max_part_minutes=2, speed=0.8, silence_ms=500)
    assert same == jobs


@pytest.mark.parametrize('original_mode', [0, 12000])
def test_previous_manual_plan_can_resume_but_cannot_mix_with_auto(tmp_path, original_mode):
    source = tmp_path / 'book.md'
    source.write_text('# Chapter\n\n' + 'A complete sentence.\n\n' * 2000)
    output = tmp_path / 'out'
    jobs, _, _ = build_jobs([source], output, False, job_max_chars=original_mode)
    with pytest.raises(ValueError, match='saved worker plan'):
        build_jobs([source], output, False)
    again, _, _ = build_jobs([source], output, False, job_max_chars=original_mode)
    assert again == jobs


def test_calibrated_default_targets_thirty_minutes_at_observed_reading_rate():
    # Real af_bella output at speed 0.9 averaged about 169 words/minute.
    # The previous 150 WPM baseline produced approximately 4000 words / 24 min.
    text = 'One complete sentence with six words.\n\n' * 3000
    ranges = duration_text_ranges(text, settings(speed=.9, silence_ms=250))
    first_words = len(text[:ranges[0][1]].split())
    assert 5000 <= first_words <= 5200
    assert 29.5 <= first_words / 169 <= 30.8
