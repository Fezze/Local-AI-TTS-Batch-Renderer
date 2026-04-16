# Local AI TTS Batch Renderer

Local renderer for Markdown/EPUB to speech using ONNX Runtime.

## Features

- Single run: `md_to_audio.py`
- Batch scheduler: `run_tts_batch.py`
- Provider priority with fallback (`CUDA/CPU` now, ready for more)
- Windows + Linux startup scripts
- Tests and CI for CPU paths

## Quick Start

Preflight check:

```powershell
.\.venv\Scripts\python.exe .\scripts\doctor.py --output-dir ".\out" --model-dir ".\models"
```

### Windows

```powershell
scripts\setup.ps1 -Dev
scripts\start.ps1 --input ".\book.epub" --output-dir ".\out"
```

Batch:

```powershell
.\.venv\Scripts\python.exe .\run_tts_batch.py --input ".\books" --output-dir ".\out"
```

Skip preflight in start script:

```powershell
scripts\start.ps1 -SkipDoctor --input ".\book.epub" --output-dir ".\out"
```

### Linux

```bash
bash scripts/setup.sh --dev
bash scripts/start.sh --input ./book.epub --output-dir ./out
```

Batch:

```bash
./.venv/bin/python ./run_tts_batch.py --input ./books --output-dir ./out
```

Skip preflight in start script:

```bash
SKIP_DOCTOR=1 bash scripts/start.sh --input ./book.epub --output-dir ./out
```

## Provider Configuration

Single run:

```powershell
python md_to_audio.py --input "book.epub" --providers "CUDAExecutionProvider,CPUExecutionProvider"
```

Batch run:

```powershell
python run_tts_batch.py --input "books" --gpu-workers 2 --cpu-workers 1 --providers "CUDAExecutionProvider,DmlExecutionProvider,CPUExecutionProvider"
```

- `--providers` defines priority order.
- Scheduler automatically builds workers from available providers.
- If GPU provider is not available, CPU workers are used.

## Project Structure

- `src/local_tts_renderer/cli.py` - thin CLI entrypoint
- `src/local_tts_renderer/cli_core.py` - minimal compatibility shim for `main` and `parse_args`
- `src/local_tts_renderer/cli_entry.py` - single-run orchestration
- `src/local_tts_renderer/cli_render_flow.py` - TTS render flow
- `src/local_tts_renderer/scheduler.py` - batch scheduler
- `src/local_tts_renderer/sources/` - source ingesters, source registry, and normalized document model
- `src/local_tts_renderer/document_helpers.py` - source-agnostic naming and grouping helpers
- `src/local_tts_renderer/providers.py` - provider resolution and worker allocation
- `src/local_tts_renderer/chunking.py` - chunking interface
- `src/local_tts_renderer/input_parsers.py` - legacy compatibility facade; new internal code should use `sources/`
- `scripts/` - setup/start scripts
- `tests/` - regression and unit tests

## Source Ingestion

Source format support is registry-driven. Each ingester normalizes input into `SourceDocument`,
`SourceMetadata`, `SourceChapter`, and optional `SourceNavigationNode` objects before rendering
or batch planning. Adding a future format should mostly mean adding one ingester module,
registering it, and testing its normalized document output.

`input_parsers.py` is kept only as a backward-compatible facade for old callers. New internal
code should not import from it or add parsing logic there. Compatibility shims such as
`cli_core.py`, `render.py`, and `chunking.py` should stay small and explicit instead of becoming
new ownership hubs.

Chapter cache files intentionally store chapter payloads only. Metadata and navigation are still
loaded through the source registry so grouped output, numbering, and tags continue to come from
the normalized source document.

## Troubleshooting

- No GPU provider detected: verify ONNX Runtime GPU package and driver stack.
- Worker timeout in batch: increase `--worker-silence-timeout-seconds`.
- Existing outputs skipped: use `--fresh` in batch or `--force` in single run.
- After interrupted/frozen run on Windows: `powershell -ExecutionPolicy Bypass -File .\scripts\recover-after-abort.ps1 -ClearResume`

## Notes

- Current default model pipeline is Kokoro ONNX.
- Planned support for additional models and formats is tracked in `BACKLOG.md`.

## Tests and Coverage

```powershell
.\.venv\Scripts\python.exe -m pytest --cov=src/local_tts_renderer --cov-report=term-missing -q
```
