from __future__ import annotations

from dataclasses import replace
import re

from .scheduler_types import ChapterJob
from .sources.model import SourceChapter

DEFAULT_JOB_MAX_CHARS = 12000


def natural_text_ranges(text: str, target: int) -> list[tuple[int, int]]:
    """Partition exactly, preferring paragraphs; never cut inside a sentence."""
    if target <= 0 or len(text) <= target:
        return [(0, len(text))]
    paragraphs = [match.end() for match in re.finditer(r'\n\s*\n', text)]
    # Preserve punctuation and whitespace in the preceding slice. Conservative
    # sentence starts avoid interpreting decimals and most abbreviations as ends.
    abbreviations = {'mr', 'mrs', 'ms', 'dr', 'prof', 'sr', 'jr', 'st', 'vs', 'etc', 'fig', 'no', 'vol', 'np', 'tj', 'tzn'}
    sentences = []
    for match in re.finditer(r'''[.!?…]["'”’»)]*\s+(?=[A-ZÀ-ÖØ-ÞĄĆĘŁŃÓŚŹŻ0-9“"'‘«])''', text):
        if text[match.start()] == '.':
            token = re.search(r"(\w+)$", text[:match.start()])
            if token and (len(token[0]) == 1 or token[0].lower() in abbreviations):
                continue
        sentences.append(match.end())
    boundaries = sorted(set([*paragraphs, *sentences, len(text)]))
    ranges = []
    start = 0
    while start < len(text):
        limit = start + target
        if limit >= len(text):
            end = len(text)
        else:
            candidates = [end for end in paragraphs if start < end <= limit and text[start:end].strip()]
            if not candidates:
                candidates = [end for end in sentences if start < end <= limit and text[start:end].strip()]
            end = max(candidates) if candidates else next(end for end in boundaries if end > start and text[start:end].strip())
        if not text[end:].strip():
            end = len(text)
        ranges.append((start, end))
        start = end
    return ranges


def select_chapter_text(chapter: SourceChapter, start: int = 0, end: int | None = None) -> SourceChapter:
    end = len(chapter.text) if end is None else end
    if not 0 <= start < end <= len(chapter.text):
        raise ValueError('Invalid chapter text range; regenerate the batch plan for this source.')
    return replace(chapter, text=chapter.text[start:end])


def split_chapter_job(job: ChapterJob, chapter: SourceChapter, target: int,
                      chunk_chars: int) -> list[ChapterJob]:
    ranges = natural_text_ranges(chapter.text, target)
    if len(ranges) == 1:
        return [job]
    return [replace(
        job,
        output_name=f'{job.output_name}-segment-{index:04d}',
        text_start=start, text_end=end, segment_index=index, segment_count=len(ranges),
        estimated_chars=end-start,
        estimated_chunks=max(1, (end-start+chunk_chars-1)//chunk_chars),
    ) for index, (start, end) in enumerate(ranges, 1)]
