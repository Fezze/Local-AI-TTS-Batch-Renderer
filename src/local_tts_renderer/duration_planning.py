"""Deterministic pre-render estimates; actual audio remains duration-limited."""
from bisect import bisect_left, bisect_right
import math
import re

from .work_planning import text_boundaries

# Calibrated against af_bella at speed 0.9: approximately 169 spoken WPM.
# Normalize to speed 1 and account separately for estimated inter-chunk pauses.
WORDS_PER_MINUTE = 190.0
DURATION_TOLERANCE = 0.1


def duration_plan_settings(minutes: float, speed: float, silence_ms: int,
                           chunk_chars: int) -> dict:
    if not math.isfinite(minutes) or minutes <= 0:
        raise ValueError('--max-part-minutes must be finite and positive')
    if not math.isfinite(speed) or speed <= 0:
        raise ValueError('--speed must be finite and positive')
    if silence_ms < 0 or chunk_chars <= 0:
        raise ValueError('Duration planning requires nonnegative silence and positive chunk size')
    return dict(version=1, minutes=minutes, speed=speed, silence_ms=silence_ms,
                chunk_chars=chunk_chars, words_per_minute=WORDS_PER_MINUTE,
                tolerance=DURATION_TOLERANCE)


def duration_text_ranges(text: str, settings: dict) -> list[tuple[int, int]]:
    """Aim at the target time, favoring paragraphs within the 10% tolerance.

    Count words at each natural boundary and approximate inter-chunk silence.
    Estimates intentionally do not change as workers finish: retries and resume
    must retain exactly the same text ranges. Numbers/abbreviations and individual
    voices can differ from this heuristic; the renderer enforces the audio cap.
    """
    if not text:
        return [(0, 0)]
    words = [m.end() for m in re.finditer(r"\w+(?:['’-]\w+)*", text)]
    seconds_per_word = 60 / (settings['words_per_minute'] * settings['speed'])
    pause_per_char = settings['silence_ms'] / (1000 * settings['chunk_chars'])

    def elapsed(end: int) -> float:
        return bisect_right(words, end) * seconds_per_word + end * pause_per_char

    paragraphs, sentences = text_boundaries(text)
    # Blank trailing space belongs to the final range, never to a separate task.
    last_content = len(text.rstrip())
    paragraphs = [end for end in paragraphs if end < last_content]
    boundaries = sorted({end for end in sentences + paragraphs if end < last_content} | {len(text)})
    times = [elapsed(end) for end in boundaries]
    paragraph_times = [elapsed(end) for end in paragraphs]
    target = settings['minutes'] * 60
    lower, upper = target * (1 - settings['tolerance']), target * (1 + settings['tolerance'])
    ranges = []
    start = 0
    while start < len(text):
        origin = elapsed(start)
        if times[-1] - origin <= upper:
            end = len(text)
        else:
            def nearest(ends: list[int], values: list[float], within_window: bool) -> int | None:
                index = bisect_left(values, origin + target)
                candidates = [i for i in (index - 1, index) if 0 <= i < len(ends) and ends[i] > start]
                if within_window:
                    candidates = [i for i in candidates if lower <= values[i] - origin <= upper]
                return ends[min(candidates, key=lambda i: abs(values[i] - origin - target))] if candidates else None

            end = nearest(paragraphs, paragraph_times, True)
            if end is None:
                end = nearest(boundaries, times, True)
            if end is None:
                # A single long sentence may exceed the target; do not cut it.
                end = boundaries[bisect_left(times, origin + target)]
        ranges.append((start, end))
        start = end
    return ranges
