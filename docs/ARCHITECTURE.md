# Architecture

## Processing flow

```text
Markdown / EPUB
      |
      v
sources registry -> SourceDocument -> single CLI or batch scheduler
                                         |
                                         v
                                    render_audio
                                         |
                                         v
                              audio + JSON manifest
```

All source formats must normalize into `SourceDocument`, `SourceMetadata`,
`SourceChapter`, and optional `SourceNavigationNode` objects before orchestration.
CLI and scheduler code must not branch on individual file extensions.

## Module ownership

| Area | Owner modules |
| --- | --- |
| Source normalization | `sources/registry.py`, `sources/markdown.py`, `sources/epub.py`, `sources/model.py` |
| Source-agnostic naming and grouping | `document_helpers.py` |
| Input glob expansion, collision checks and cache identity | `input_paths.py` |
| Atomic local file publication | `atomic_io.py` |
| Model provisioning and validation | `model_bootstrap.py` |
| Single-run argument/runtime bootstrap | `cli_runtime.py` |
| Single-run orchestration | `cli_entry.py` |
| Chunking and rendering | `cli_chunking_utils.py`, `cli_audio_utils.py`, `cli_part_writer.py`, `cli_render_flow.py` |
| Resume state and scoped cleanup | `cli_resume.py`, `cli_render_cleanup.py` |
| Batch arguments and planning | `scheduler_args.py`, `scheduler_jobs.py`, `scheduler_setup.py` |
| Batch worker lifecycle | `scheduler_runtime.py`, `scheduler_process.py`, `scheduler_logging.py` |
| Worker progress deadline and failure presentation | `scheduler_progress.py`, `scheduler_failures.py` |
| Composition root | `scheduler_core.py` |

`input_parsers.py`, `cli_core.py`, `render.py`, `chunking.py`, `cli.py`, and
`scheduler.py` are compatibility surfaces. They must stay small and must not
become implementation owners.

## Stable public surface

The intended stable entrypoints are:

- `local_tts_renderer.tts_main`
- `local_tts_renderer.batch_main`
- `local_tts_renderer.cli.main`
- `local_tts_renderer.scheduler.main`
- `local_tts_renderer.sources.load_source`
- `local_tts_renderer.sources.supported_suffixes`

Compatibility exports are protected by architecture tests. New internal code
should import from the owning module rather than a compatibility facade.

## Invariants

- Rendering changes require a regression test or behavior snapshot.
- Production code files must remain at or below 500 lines.
- New input formats are registered ingesters returning `SourceDocument`.
- Chapter caches contain chapter payloads only; source metadata and navigation
  still come from the source registry.
- Output manifests and chunking snapshots are compatibility artifacts, not
  incidental test data.
- Relative intra-package imports are the project convention.

## Accepted decisions

- Markdown and EPUB ingestion use a shared normalized source model.
- Markdown-only options are contained in `MarkdownIngestOptions`.
- CLI and batch orchestration use the source registry instead of format-specific
  branches.
- Legacy import surfaces remain explicit shims until a deliberate compatibility
  break is planned.
- Model artifacts are checksum-validated, published atomically, and protected by
  a cross-process bootstrap lock.
- Resume checkpoints are atomic, fingerprinted against source, model identity,
  and output-affecting configuration. Chunk size stays pinned across partial
  continuation and worker retry.
- Final manifests are published atomically and count as complete only when their
  source content, chunk/part coverage, and owned non-empty artifacts validate.
- Cleanup derives owned paths from the job output identity; paths embedded in a
  checkpoint or manifest never grant deletion authority.
- Batch completion is unsuccessful whenever jobs fail or remain pending, and
  final logs retain all three job counts.
- Batch workers receive an explicit MP3-only or MP3+WAV flag.
- Reject colliding source output slugs within an invocation before cache/output
  mutation; preserve legacy output names instead of automatically renaming them.
  Chapter cache filenames include a SHA-256 hash of the resolved source path.
- Installed and checkout batch workers execute the CLI module. The scheduler
  passes its package root through PYTHONPATH; the legacy script_path argument
  remains accepted by worker-command helpers for compatibility.
- Packaging reads dependencies from requirements.txt and retains the existing GPU
  profile. Setup/start wrappers use repository-relative paths; direct CLI commands
  use caller-relative paths. Both default to `out`.
- An optional monotonic progress deadline starts at the render-start event. Only
  increasing completed-chunk counts reset it. It is disabled by default and uses
  existing worker termination/retry behavior when enabled.
- NCX links resolve relative to the NCX document. TOC recursion shares its lookup
  even when a parent has no link; spine order remains the reading order.
- Future formats and TTS models must first define a tested contract; they should
  not expand existing orchestration modules with new branches.

Active architectural work is tracked in [../BACKLOG.md](../BACKLOG.md), especially
items `B-201`, `B-301`, and `B-302`.
