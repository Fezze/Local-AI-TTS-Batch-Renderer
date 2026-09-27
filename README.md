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

The examples below start in the repository root. Setup and start wrappers also
work when invoked by absolute path from another directory. They run from the
repository root, so relative input, output and model arguments passed to wrappers
are resolved there. Direct Python and installed console commands resolve relative
paths from the caller's current directory. The default output directory is `out`.

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

### Installed commands

After setup, install the package in the same environment:

```bash
./.venv/bin/python -m pip install .
local-tts-render --help
local-tts-batch --help
```

Use the environment's Python and console paths on Windows. Activate the environment
or use the full path to its console scripts. Editable installation (`pip install -e .`)
is also supported. Package dependencies come from `requirements.txt`; the existing
GPU dependency profile is unchanged. Batch workers invoke `python -m local_tts_renderer.cli`
and do not require repository entrypoint scripts.

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
bootstrap consumes `--model-dir`, while preflight recognizes `--output-dir` / `--out`,
`--model-dir`, and `--providers`.

## Input identity and worker timeouts

Both CLIs accept quoted absolute and relative glob patterns. Matches are sorted
and duplicate paths are processed once. Sources in one invocation must map to
distinct output directories: for example, `a/book.md` and `b/book.md`, or
`a-b.md` and `a_b.md`, collide. Rendering exits with code 2 and lists those inputs
before writing caches or deleting outputs. Rename sources or run them separately
with distinct output directories. Existing result names are unchanged; caches use
an additional source-path hash and can be regenerated automatically.

Batch `--worker-progress-timeout-seconds N` optionally retries a renderer that
finishes no further chunks for N seconds after rendering starts. Its default is
`0` (disabled). Repeated heartbeats with the same completed count do not reset it;
an increasing completed count does. The limit also applies while publishing audio
parts, so allow enough time for slow encoding. Existing silence and bootstrap
limits remain separate. Timeout logs distinguish `no_progress` from `silence`.

## Persistent model sessions

`--worker-mode persistent` (the default) keeps one Python process and one model session per worker.
The model and warmup run once; subsequent segments reuse the session and the
eSpeak phonemization backend, avoiding repeated native-library loads. Each worker
runs one task at a time. GPU/CPU worker counts, text boundaries, audio settings and
output names are the same in both modes. Use `--worker-mode subprocess` to launch
a separate process for each task.

```bash
bash scripts/start-batch.sh --input book.epub --gpu-workers 2 --cpu-workers 1 --worker-mode persistent
```

Persistent workers read a content-addressed document snapshot prepared by the batch
scan, including metadata and navigation, and retain only the latest document in
memory. The existing chapter cache remains available. A failed session is discarded;
retry starts a new process and resumes the task's validated checkpoint. Idle sessions
do not trigger progress timeouts. Interrupting a batch also closes idle sessions.
Sessions retain model memory while idle, until their worker thread exits.

Worker logs include initialization and document-preparation timings. Persistent
result events also include task time and current RSS on Linux. To compare modes
with fixed assignments (two tasks per worker, two CUDA workers and one CPU worker):

```bash
PYTHONPATH=src ./.venv/bin/python scripts/benchmark_workers.py --input book.epub --output /tmp/tts-benchmark --repeats 3
```

The benchmark requires CUDA, `nvidia-smi`, and `ffmpeg`; memory sampling uses Linux
`/proc`. Choose a new output directory. It selects six naturally delimited segments
of about 2000 characters, alternates mode order, validates actual session providers,
compares manifest text and decodes every MP3. `results.json` contains elapsed time,
audio duration, RTF (render time / audio duration), sampled peak worker RAM/VRAM and
session/task metrics. Sampling once per second can miss short memory peaks. Run it
without other rendering or test workloads. Add `--soak-repeats 3` to run six
tasks per persistent session and record RSS after each task in
`memory-soak/measurement.json`. These are real inference measurements;
synthetic-audio regression tests validate behavior separately.

Measured on 2026-09-23 with two CUDA workers sharing an RTX 5070 and one CPU
worker, using six segments of *Eric* and three runs per mode:

| Mode | Run times (s) | Median (s) | Highest sampled RAM (GiB) | Highest sampled VRAM (GiB) |
| --- | --- | --- | --- | --- |
| subprocess | 48.82 / 49.78 / 49.87 | 49.78 | 4.68 | 2.40 |
| persistent | 45.68 / 46.65 / 46.80 | 46.65 | 4.65 | 2.39 |

Each run produced 734.05 seconds of audio with identical manifest text and audio
part durations; every MP3 decoded successfully. Persistent mode reduced median
elapsed time by **6.3%**, with three model initializations instead of six. RAM is
summed worker RSS, including shared pages; VRAM is summed per-process allocation.

An additional 18-task run reused each session for six tasks. RSS rose while ONNX
allocators warmed up, then stabilized for repeated inputs: the final two tasks
changed each worker's RSS by less than 0.02 MiB. Final RSS was about 1765/1701 MiB
for the GPU workers and 2263 MiB for CPU. Session buffers remain allocated until
worker exit; other input shapes can change memory requirements. Phoneme equality
and native library reuse also have real eSpeak regression coverage.

The measured improvement and passing regressions enable persistent mode by default.
The subprocess mode remains available for workload-specific comparisons. See the
[measurement summary](docs/benchmarks/persistent-workers-2026-09-23.json) for exact
values, hardware, package versions and per-task memory observations.

## Chapters, worker tasks and audio files

EPUB reading order comes from the spine, while logical sections come from EPUB 3
navigation or NCX targets (including `#fragment` anchors), semantic chapter markers
and headings. Unmarked continuation files stay in the preceding section; a file
boundary alone no longer creates a chapter. If no structural markers exist, the
text remains one logical section. A single navigation link does not hide later
chapter headings, and an empty chapter anchor can introduce text in the next file.
Inline emphasis stays inside its paragraph.

Batch workers can process different ranges of the same logical chapter in parallel:

```bash
bash scripts/start-batch.sh --input book.epub --gpu-workers 2 --cpu-workers 1 --job-max-chars 12000
```

`--job-max-chars` defaults to 12000 and is a soft task-size target. Splits prefer
paragraph endings; an oversized paragraph is split at sentence endings. A sentence
longer than the target stays whole, and a chapter heading is kept with its first
text segment. `--job-max-chars 0` disables task segmentation. Small chapters keep
their existing output names; split chapters use ordered `-segment-0001` suffixes.
These task boundaries are separate from the inference chunk limits (`--max-chars`,
`--max-phoneme-chars`) and the audio part limit (`--max-part-minutes`, default 30).
A task can produce several audio parts or finish with a shorter file; parts are not
padded or merged to reach 30 minutes.

Interrupting a batch stops further task launches and retries before terminating
workers. Workers have up to 10 seconds to finish a fragment and save progress;
after forced termination, resume uses the last available checkpoint.

Each task retains the logical chapter index and exact text offsets, with its own
checkpoint, manifest and audio paths. Completed tasks are skipped and interrupted
tasks resume their pinned chunk size. The saved work plan prevents mixing different
source contents or task-size settings in the same output directory. To change those,
use a new `--output-dir`; `--fresh` resets partial rendering, not the saved work plan.
When EPUB interpretation changes existing chapter boundaries, use a new output
directory for that book rather than combining old and new layouts.

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
