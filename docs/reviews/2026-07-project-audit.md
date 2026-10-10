# Project audit — 2026-07-16

Scope: tracked Markdown files, architecture, setup/start scripts, dependency
layout, CLI behavior, scheduler failure paths, and the local test suite at commit
`4d2df6f`.

## Baseline

- Git worktree was clean before documentation changes.
- 87 tests passed on Python 3.12.
- Measured coverage: 84.66% across 2,380 statements.
- No production file exceeded 500 lines; the largest were
  `scheduler_runtime.py` (472) and `cli_render_flow.py` (457).
- Shell scripts passed `bash -n`.
- PowerShell was not available locally; those scripts were reviewed statically.
- The local `.venv/bin/python` symlink was stale after an IDE sandbox revision;
  tests were run with the current Python 3.12 interpreter and the existing
  environment packages.

## Strengths

- Source ingestion has a clear registry and normalized document model.
- Compatibility shims are small and protected by architecture tests.
- Manifest ordering and chunking have regression snapshots.
- Scheduler runtime, retry paths, parsers, providers, and source loaders have
  useful unit coverage.
- The repository is compact and already respects the 500-line production limit.

## Findings

| Priority | Finding | Evidence |
| --- | --- | --- |
| P0 | A fresh clone cannot reach automatic model download through start wrappers because doctor fails on missing models first. | `scripts/start.sh`, `scripts/start.ps1`, `scripts/doctor.py`, `cli_runtime.py` |
| P0 | Batch returns exit code 0 even when jobs permanently fail. | `scheduler_core.py` final return path |
| P0 | A crash before the first part checkpoint can leave partial audio that blocks retry with `FileExistsError`. | `cli_render_flow.py` writer initialization and resume cleanup |
| P0 | Batch parses `--no-mp3-only` but does not pass it to child workers, whose default remains MP3-only. | `scheduler_args.py`, `scheduler_jobs.py`, `cli_runtime.py` |
| P0 | `--fresh` requeues completed jobs without granting overwrite, while `--force` alone does not disable completed-job skipping. | `scheduler_core.py`, `scheduler_jobs.py`, `scheduler_runtime.py`, `cli_render_flow.py` |
| P1 | Two inputs with the same normalized stem can target the same output tree. | `cli_entry.py`, `scheduler_jobs.py` |
| P1 | Resume state is non-atomic and has no source/configuration fingerprint. | `cli_audio_utils.py`, `cli_render_flow.py` |
| P1 | GPU-to-CPU retry can become unserviceable when no CPU worker exists. | `scheduler_runtime.py`, `scheduler_jobs.py` |
| P1 | Heartbeats count as liveness even when chunk progress is frozen. | `cli_runtime.py`, `scheduler_runtime.py` |
| P1 | The default `".\\out"` creates a literal backslash path on Linux and differs from doctor defaults. | `defaults.py`, CLI parsers, `doctor.py` |
| P1 | Setup installs a CUDA-oriented environment even for CPU tests; package metadata and console entrypoints are not exercised by setup or CI. | `requirements.txt`, `pyproject.toml`, CI workflow |
| P2 | EPUB navigation loses multiple anchor targets in one XHTML and lacks EPUB3 navigation support. | `sources/epub.py` |

## Documentation findings

- Quick Start placed preflight before setup/model bootstrap.
- Provider examples implied DirectML support without an install profile.
- `--fresh` was described as the remedy for existing output, although overwrite
  also requires `--force` in the current implementation.
- Architecture decisions were duplicated across README and backlog.
- `REQUESTED_CHANGES.md` marked the MP3 toggle complete despite the missing batch
  propagation regression.

## Result

The documentation was reorganized around one active backlog, one architecture
reference, one development guide, and dated review archives. No rendering logic
was changed during this audit. Implementation order and acceptance criteria are
defined in [../../BACKLOG.md](../../BACKLOG.md).
