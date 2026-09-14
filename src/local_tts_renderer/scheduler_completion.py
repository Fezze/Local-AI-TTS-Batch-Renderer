from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .cli_render_cleanup import load_complete_manifest
from .document_helpers import slugify
from .scheduler_types import ChapterJob
from .sources import SourceChapter


CompletionCache = dict[tuple[Path, Path, str | None, tuple[tuple[str, str], ...]], bool]


def is_job_complete(
    output_dir: Path,
    job: ChapterJob,
    expected_chapter: SourceChapter,
    expected_document_chapters: Sequence[SourceChapter],
    completion_cache: CompletionCache | None = None,
) -> bool:
    primary_output_root = output_dir / job.output_subdir
    source_output_root = output_dir / slugify(job.source_path.stem)
    candidate_manifests: list[tuple[Path, Path, str | None, Sequence[SourceChapter]]] = [
        ((primary_output_root / job.output_name).with_suffix(".json"), primary_output_root, job.output_name, [expected_chapter]),
        (source_output_root.with_suffix(".json"), source_output_root, None, expected_document_chapters),
    ]
    group_manifest_path = primary_output_root.with_suffix(".json")
    if group_manifest_path not in {candidate[0] for candidate in candidate_manifests}:
        group_chapters = [chapter for chapter in expected_document_chapters if chapter.group == expected_chapter.group]
        candidate_manifests.insert(1, (group_manifest_path, primary_output_root.with_suffix(""), None, group_chapters))

    for manifest_path, output_root, final_stem_override, expected_chapters in candidate_manifests:
        key = (manifest_path, output_root, final_stem_override, tuple((chapter.title, chapter.text) for chapter in expected_chapters))
        if completion_cache is not None and key in completion_cache:
            if completion_cache[key]:
                return True
            continue
        complete = False
        if manifest_path.exists():
            try:
                complete = load_complete_manifest(
                    manifest_path=manifest_path,
                    base_output_dir=output_dir,
                    output_root=output_root,
                    final_stem_override=final_stem_override,
                    expected_chapters=list(key[-1]),
                ) is not None
            except OSError:
                pass
        if completion_cache is not None:
            completion_cache[key] = complete
        if complete:
            return True
    return False


__all__ = ["CompletionCache", "is_job_complete"]