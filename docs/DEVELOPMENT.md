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

Linux setup detects a broken `.venv` and recreates it. This is particularly
useful when a virtual environment points into an old sandboxed IDE revision.
Windows parity is tracked as backlog item `B-104`.

Runtime dependencies currently live in `requirements.txt`, and development
dependencies in `requirements-dev.txt`. Packaging and install profiles are not
yet authoritative in `pyproject.toml`; use the setup scripts until `B-101` is
complete.

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
