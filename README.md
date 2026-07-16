# Local AI TTS Batch Renderer

Local Markdown and EPUB to speech renderer based on Kokoro ONNX. It supports a
single-process CLI and a multi-worker batch scheduler on Windows and Linux.

## Current status

- Inputs: `.md`, `.markdown`, and `.epub`.
- Output: MP3 by default, optional WAV and per-chunk files, plus JSON manifests.
- Runtime: CUDA when available, with CPU fallback.
- Python: `>=3.11`; CI currently covers 3.11 and 3.12.
- Tests: unit, scheduler-flow, architecture, manifest, and chunking regressions.

DirectML and ROCm names are recognized by provider routing, but their dependency
profiles and installation instructions are not complete. Treat them as planned,
not supported setup paths. See [BACKLOG.md](BACKLOG.md).

## Quick start

Run commands from the repository root.

### Linux

```bash
bash scripts/setup.sh --dev
```

```bash
bash scripts/start.sh --input ./book.epub --output-dir ./out
bash scripts/start-batch.sh --input ./books --output-dir ./out
```

### Windows PowerShell

```powershell
.\scripts\setup.ps1 -Dev
```

```powershell
.\scripts\start.ps1 --input ".\book.epub" --output-dir ".\out"
.\scripts\start-batch.ps1 --input ".\books" --output-dir ".\out"
```

Start wrappers download and validate the Kokoro model and voice data before the
doctor runs. Downloads use a lock and atomic replacement, so parallel workers do
not publish partial model files. The default destination is `models/`; pass
`--model-dir` to select another location.

## Preflight

Run the doctor explicitly when diagnosing the environment:

```bash
./.venv/bin/python scripts/doctor.py --output-dir ./out --model-dir ./models
```

```powershell
.\.venv\Scripts\python.exe .\scripts\doctor.py --output-dir ".\out" --model-dir ".\models"
```

The doctor checks Python, paths, model files, ONNX providers, the temporary
directory, and Python syntax. Start wrappers forward the original arguments;
bootstrap consumes `--model-dir`, while preflight recognizes `--output-dir`,
`--model-dir`, and `--providers`.

## Common usage

Inspect chapters without rendering:

```bash
./.venv/bin/python md_to_audio.py --input ./book.epub --list-chapters
```

All start wrappers show `--help` without provisioning models. The single-run
wrappers also skip model bootstrap and model-dependent preflight for
`--list-chapters` and `--wav-to-mp3` operations.

Render selected inputs in one process:

```bash
./.venv/bin/python md_to_audio.py \
  --input ./chapter-1.md ./chapter-2.md \
  --output-dir ./out \
  --providers "CUDAExecutionProvider,CPUExecutionProvider"
```

Run the batch scheduler:

```bash
./.venv/bin/python run_tts_batch.py \
  --input ./books \
  --output-dir ./out \
  --gpu-workers 2 \
  --cpu-workers 1
```

Recovery and overwrite behavior for batch jobs:

| Flags | Unfinished job | Completed job |
| --- | --- | --- |
| none | Resume its validated checkpoint. | Skip. |
| `--fresh` | Remove its partial artifacts and restart. | Skip. |
| `--force` | Resume its validated checkpoint. | Remove owned output and rerender. |
| `--fresh --force` | Remove owned output and restart. | Remove owned output and rerender. |

The single-process CLI uses the same unfinished-job recovery rules. It remains
fail-fast on completed output unless `--force` is supplied.

Other useful flags:

| Flag | Meaning |
| --- | --- |
| `--mp3-only` / `--no-mp3-only` | Select MP3-only or MP3+WAV in single and batch runs. |
| `--md-single-chapter` | Treat a Markdown file as one chapter. |
| `--md-chapter-heading-level 0-4` | Control Markdown heading-based chapter splitting; `0` selects automatically. |
| `--max-chapter-chars N` | Split oversized Markdown chapters; `0` disables this extra split. |

## Provider status

| Provider | Repository status |
| --- | --- |
| `CUDAExecutionProvider` | Current default GPU path. The existing dependency set is CUDA-oriented. |
| `CPUExecutionProvider` | Current fallback and test path, but installation is still coupled to GPU dependencies. |
| `DmlExecutionProvider` | Routing scaffold only; no supported DirectML install profile yet. |
| `ROCMExecutionProvider` | Routing scaffold only; no supported ROCm install profile yet. |

Provider priority is selected with `--providers`. If no requested GPU provider is
available, the scheduler creates CPU workers.

## Output and recovery

Each source writes audio, JSON manifests, and atomic resume checkpoints under its
output subtree. Batch runs additionally write `runner.jsonl`, per-job logs, and a
chapter cache. Completed batch jobs are normally skipped.

After an interrupted Windows run, first preserve the checkpoint:

```powershell
.\scripts\recover-after-abort.ps1
```

Use `-ClearResume` only when intentionally discarding resume state:

```powershell
.\scripts\recover-after-abort.ps1 -ClearResume
```

## Architecture

Source ingestion is registry-driven. Markdown and EPUB ingesters normalize data
into `SourceDocument` before single-run or batch orchestration. Compatibility
modules remain intentionally thin.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for module ownership, stable
entrypoints, and architectural invariants.

## Development

```bash
./.venv/bin/python -m pytest --cov=src/local_tts_renderer --cov-report=term-missing -q
```

Windows:

```powershell
.\.venv\Scripts\python.exe -m pytest --cov=src/local_tts_renderer --cov-report=term-missing -q
```

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for regression expectations and
environment repair. Active work lives only in [BACKLOG.md](BACKLOG.md). The latest
review snapshot is [docs/reviews/2026-07-project-audit.md](docs/reviews/2026-07-project-audit.md).
