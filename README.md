# Local AI TTS Batch Renderer

Local renderer for Markdown/EPUB to speech using ONNX Runtime.

## Features

- Single run: `md_to_audio.py`
- Batch scheduler: `run_tts_batch.py`
- Provider priority with fallback (`CUDA/CPU` now, ready for more)
- Windows + Linux startup scripts
- Tests and CI for CPU paths

## Quick Start

### Windows

```powershell
scripts\setup.ps1 -Dev
scripts\start.ps1 --input ".\book.epub" --output-dir ".\out"
```

Batch:

```powershell
.\.venv\Scripts\python.exe .\run_tts_batch.py --input ".\books" --output-dir ".\out"
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

- `src/local_tts_renderer/cli.py` - TTS CLI/render flow
- `src/local_tts_renderer/scheduler.py` - batch scheduler
- `src/local_tts_renderer/providers.py` - provider resolution and worker allocation
- `src/local_tts_renderer/chunking.py` - chunking interface
- `src/local_tts_renderer/input_parsers.py` - parser interface
- `scripts/` - setup/start scripts
- `tests/` - regression and unit tests

## Troubleshooting

- No GPU provider detected: verify ONNX Runtime GPU package and driver stack.
- Worker timeout in batch: increase `--worker-silence-timeout-seconds`.
- Existing outputs skipped: use `--fresh` in batch or `--force` in single run.
- After interrupted/frozen run on Windows: `powershell -ExecutionPolicy Bypass -File .\scripts\recover-after-abort.ps1 -ClearResume`

## Notes

- Current default model pipeline is Kokoro ONNX.
- Planned support for additional models and formats is tracked in `BACKLOG.md`.
