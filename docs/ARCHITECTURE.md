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
| Source normalization | `sources/registry.py`, `sources/markdown.py`, `sources/epub.py`, `sources/epub_navigation.py`, `sources/epub_structure.py`, `sources/model.py` |
| Source-agnostic naming and grouping | `document_helpers.py` |
| Input glob expansion, collision checks and cache identity | `input_paths.py` |
| Natural worker text ranges and immutable work-plan identity | `work_planning.py`, `work_plan_state.py` |
| Atomic local file publication | `atomic_io.py` |
| Model provisioning and validation | `model_bootstrap.py` |
| Single-run argument/runtime bootstrap | `cli_runtime.py` |
| Single-run orchestration | `cli_entry.py` |
| Chunking and rendering | `cli_chunking_utils.py`, `cli_audio_utils.py`, `cli_part_writer.py`, `cli_render_flow.py` |
| Resume state and scoped cleanup | `cli_resume.py`, `cli_render_cleanup.py` |
| Batch arguments, scan cache and planning | `scheduler_args.py`, `scheduler_scan.py`, `scheduler_completion.py`, `scheduler_jobs.py`, `scheduler_setup.py` |
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
- Source scan caches contain normalized documents and are invalidated when the
  source file or ingestion options change. Identity includes a SHA-256 content
  digest, so preserving size and modification time cannot conceal edits. The cache
  version also changes when normalized ingestion semantics change.
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
- EPUB resource boundaries do not imply logical chapter boundaries. Navigation
  targets keep fragments; EPUB 3 toc navigation takes precedence over NCX. Exact
  navigation fragments define sections, with semantic markers/headings as fallback.
  A single navigation target does not suppress later headings. An empty chapter
  anchor remains pending across resource boundaries until its text arrives.
  Unmarked resources continue the preceding section in spine order. Block paragraphs
  retain separation while inline elements retain text continuity.
- A SourceChapter represents logical content. A ChapterJob may select an exact
  [text_start, text_end) range of that chapter; it never invents a source chapter.
  Workers receive these offsets alongside the original chapter index and cache.
- Batch task sizing is independent of inference chunks and audio duration limits.
  The default 12000-character target prefers paragraphs, then sentence boundaries;
  overlong sentences remain intact and headings stay with following text. All ranges
  concatenate exactly to the original chapter. Bounded segments are CPU-eligible
  while GPU workers are active.
- Segment ordinals determine output and log identity independently of completion
  order. Checkpoints fingerprint the selected text; retries keep the same range and
  chunk size. An existing unsplit checkpoint keeps its original task identity.
- A saved source/work-size signature rejects incompatible plans before rendering.
  Changing source content or --job-max-chars requires a new output directory.
  --fresh discards rendering progress without changing that plan.
- Batch interruption stops queue selection and retries before terminating workers.
  Process creation/registration and the stop request share the scheduler lock;
  workers waiting for GPU bootstrap also check the stop request before spawning.
  Worker threads finish before temporary directories are removed. Forced termination
  only guarantees access to checkpoints already written.
- Future formats and TTS models must first define a tested contract; they should
  not expand existing orchestration modules with new branches.

Active architectural work is tracked in [../BACKLOG.md](../BACKLOG.md), especially
items `B-201`, `B-301`, and `B-302`.

EPUB navigation reference: [W3C EPUB 3.3](https://www.w3.org/TR/epub-33/).
