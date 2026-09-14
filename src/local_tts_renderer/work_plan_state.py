from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .atomic_io import atomic_write_text
from .input_paths import source_cache_key
from .sources.model import SourceChapter


def preserve_work_plan(output_dir: Path, source: Path, chapters: list[SourceChapter], target: int) -> None:
    """Do not mix filenames/checkpoints from two different segmentation plans."""
    if target < 0:
        raise ValueError('--job-max-chars must be nonnegative')
    payload = {'version': 1, 'job_max_chars': target,
               'chapters': [(chapter.title, chapter.text, chapter.group) for chapter in chapters]}
    signature = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    path = output_dir / '.cache' / 'work-plans' / (source_cache_key(source) + '.json')
    expected = {'version': 1, 'signature': signature, 'job_max_chars': target}
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding='utf-8'))
        except (ValueError, OSError) as exc:
            raise ValueError(f'Cannot read worker plan {path}; use a new --output-dir.') from exc
        if stored != expected:
            raise ValueError(f'Source or --job-max-chars differs from the saved worker plan for {source}. '
                             'Use the original settings/source to resume, or a new --output-dir.')
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(expected, indent=2) + '\n')
