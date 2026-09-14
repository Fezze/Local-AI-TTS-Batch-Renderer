# Development

## Environment

The project requires Python `>=3.11`; CI currently exercises 3.11 and 3.12.
Run setup from the repository root:

```bash
bash scripts/setup.sh --dev
```

```powershell
.\scripts\setup.ps1 -Dev
```

Linux and Windows setup detect a broken `.venv` and recreate it. PowerShell
setup stops immediately after a failed native command. Setup and start wrappers
resolve relative paths from the repository root, regardless of the caller directory.

Runtime dependencies live in `requirements.txt`, which also supplies the dynamic
package dependencies in `pyproject.toml`. Development and offline packaging-test
tools live in `requirements-dev.txt`. Wheel and editable installations expose
`local-tts-render` and `local-tts-batch`; separate CPU/CUDA profiles remain in B-101.

## Tests

Fast regression:

```bash
./.venv/bin/python -m pytest -q
```

Coverage:

```bash
./.venv/bin/python -m pytest \
  --cov=src/local_tts_renderer \
  --cov-report=term-missing \
  --cov-fail-under=50 \
  -q
```

Windows uses `.\.venv\Scripts\python.exe` in place of `./.venv/bin/python`.

Packaging smoke tests build and install wheel/editable packages offline into temporary
virtual environments and reuse installed runtime dependencies. They exercise both
console help commands and a worker chapter-list invocation without repository scripts.
Native Windows PowerShell tests run in the existing Windows CI matrix and skip on Linux.
Rendering tests use synthetic audio; they do not certify real GPU inference.

Expected protection by change type:

| Change | Minimum verification |
| --- | --- |
| Rendering, chunking, manifest, resume | Focused regression or snapshot plus the full suite |
| Scheduler/retry/provider routing | Runtime-flow regression plus the full suite |
| Source ingestion | Normalized-document fixture plus manifest-order regression |
| CLI flags | Parser test and exact worker-command propagation test |
| Setup/start scripts | Shell syntax check or PowerShell smoke test, then CLI `--help` |
| Documentation only | Link/path check, `git diff --check`, and relevant command verification |

Do not lower coverage to make a change pass. The current CI threshold is 50%,
while the July 2026 audit measured roughly 85%; raising the gate is tracked in
`B-104`.

## Architecture guardrails

- Keep production modules at or below 500 lines.
- Put new implementation in the owning module listed in
  [ARCHITECTURE.md](ARCHITECTURE.md), not in a compatibility facade.
- Use relative imports inside `local_tts_renderer`.
- Add source formats only through the source registry.
- Preserve public compatibility exports unless a breaking change is explicit.

## Documentation ownership

- `README.md`: current user workflow and supported behavior.
- `BACKLOG.md`: active work and acceptance criteria only.
- `docs/ARCHITECTURE.md`: stable boundaries and accepted decisions.
- `docs/reviews/`: dated, immutable review snapshots.
- `REQUESTED_CHANGES.md`: compatibility pointer only.
