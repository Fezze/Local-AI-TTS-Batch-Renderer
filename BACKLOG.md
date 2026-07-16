# Backlog

Last reviewed: 2026-07-16.

This is the single source of truth for active work. Priorities mean:

- **P0** — correctness, data integrity, or a blocked clean-clone workflow.
- **P1** — reliability, installation, and automation.
- **P2** — maintainability, performance, and broader hardware support.
- **P3** — product extensions after the current pipeline is reliable.

Every rendering change requires a regression test or behavior snapshot. A task is
done only when its acceptance criteria, tests, and user-facing documentation are
complete.

## P0 — correctness and onboarding

### B-001: Make a clean clone runnable

- [x] Replace the doctor-before-download deadlock with one explicit model bootstrap flow.
- [x] Download each model once, to a temporary file, then validate and atomically rename it.
- [x] Prevent parallel batch workers from racing to create the same model files.
- [x] Cover Linux wrapper order with mocked bootstrap, preflight, and renderer processes.
- [ ] Add native Linux and Windows CI smoke tests for setup -> preflight -> first render with mocked downloads. The current Windows regression is a static PowerShell check.
- [x] Remove the obsolete first-run bypass from `README.md`.

### B-005: Use one platform-neutral runtime configuration

- [ ] Change the default output from `".\\out"` to `"out"` and add Windows/Linux parser tests.
- [ ] Make start wrappers run from the repository root regardless of the caller's current directory.
- [ ] Forward resolved output, model, and provider arguments to the doctor.
- [ ] Remove duplicated timeout defaults from `defaults.py`.

## P1 — reliability, installation, and automation

### B-101: Introduce explicit install profiles

- [ ] Provide mutually exclusive CPU and CUDA dependency profiles; add DirectML/ROCm only when tested.
- [ ] Make `pyproject.toml` the install source of truth and create working console entrypoints.
- [ ] Decide whether this is an installable package or script-only application; remove the unused alternative.
- [ ] Add a lock/constraints update process and verify exactly one ONNX Runtime distribution per environment.

### B-102: Make output identity collision-safe

- [ ] Detect two inputs that map to the same source slug before starting workers.
- [ ] Choose a stable disambiguation scheme based on relative path or source identity.
- [ ] Cover same-name files from different directories and different names with the same normalized slug.

### B-103: Separate liveness from progress

- [ ] Track process output, heartbeat, and actual chunk progress independently.
- [ ] Time out a renderer that emits heartbeats without advancing for a configured interval.
- [ ] Add a deadlocked-render regression.

### B-104: Harden setup scripts and CI

- [ ] Make PowerShell scripts propagate every native process exit code.
- [ ] Detect and recreate broken Windows virtual environments as Linux setup already does.
- [ ] Test wheel/editable installation and both console `--help` commands in CI.
- [ ] Use a lightweight CPU profile for unit tests; keep real provider inference in a separate smoke job.
- [ ] Raise the coverage gate from 50% toward the current 85% baseline after filling critical resume branches.
- [ ] Add an automated production-file limit check for the 500-line rule.

### B-105: Complete EPUB ingestion coverage

- [ ] Preserve separate TOC anchors that point into the same XHTML file.
- [ ] Support EPUB3 navigation documents in addition to NCX.
- [ ] Add normalized-document fixtures for both cases before changing rendering.

## P2 — maintainability, performance, and hardware

### B-201: Keep the scheduler composition root small

- [ ] Move thread lifecycle and shared-state mutation out of `scheduler_core.py` behind explicit runtime services.
- [ ] Preserve CLI, worker command, retry, manifest, and scheduling behavior with regressions.
- [ ] Keep compatibility modules thin and all production files below 500 lines.

### B-202: Add conservative performance profiles

- [ ] Add an opt-in `--safe-workers` profile for weaker machines.
- [ ] Define and benchmark CPU presets for worker count and chunk sizes.
- [ ] Separate antivirus guidance from I/O diagnostics; document the risk and reversal of Defender exclusions.

### B-203: Publish a tested hardware matrix

- [ ] Document supported Python, OS, provider, driver, and dependency combinations.
- [ ] Validate DirectML on Windows and ROCm on Linux before calling AMD supported.
- [ ] Prepare an ARM64 Linux dependency and audio-runtime checklist.

### B-204: Consolidate operational utilities

- [ ] Move the ad hoc EPUB repair utility under `scripts/` or retire it after confirming it is still needed.
- [ ] Add dry-run fixtures and explicit error reporting before allowing repair mutations.

## P3 — extensibility

### B-301: Add a renderer/model contract

- [ ] Extract a model-neutral renderer interface from the Kokoro-specific runtime.
- [ ] Preserve chunking, manifests, retry semantics, and audio metadata with snapshots.
- [ ] Add a second model only after the contract is proven by tests.

### B-302: Add input formats through the source registry

- [ ] Add DOCX first as the next normalized `SourceDocument` ingester.
- [ ] Evaluate PDF and MOBI only after fixtures define ordering, metadata, navigation, and text-cleaning expectations.
- [ ] Do not add format-specific branching to CLI or scheduler modules.

## Decisions and history

- Current boundaries and completed ingestion decisions: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
- April refactor review archive: [docs/reviews/2026-04-refactor.md](docs/reviews/2026-04-refactor.md).
- July project audit: [docs/reviews/2026-07-project-audit.md](docs/reviews/2026-07-project-audit.md).
