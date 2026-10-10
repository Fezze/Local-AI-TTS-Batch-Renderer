# Backlog

Last reviewed: 2026-09-14.

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

## P1 — reliability, installation, and automation

### B-101: Introduce explicit install profiles

- [ ] Provide mutually exclusive CPU and CUDA dependency profiles; add DirectML/ROCm only when tested.
- [ ] Add a lock/constraints update process and verify exactly one ONNX Runtime distribution per environment.

### B-104: Harden setup scripts and CI

- [ ] Audit remaining recovery and Defender utility scripts for native process exit codes; setup/start scripts are covered.
- [ ] Use a lightweight CPU profile for unit tests; keep real provider inference in a separate smoke job.
- [ ] Raise the coverage gate from 50% toward the current 85% baseline after filling critical resume branches.

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
