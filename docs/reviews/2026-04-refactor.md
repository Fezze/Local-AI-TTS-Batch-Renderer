# April 2026 refactor review

Status: archived on 2026-07-16. Active follow-up work was migrated to
[../../BACKLOG.md](../../BACKLOG.md).

The original root document referenced `Local Tts Renderer Review And Refactor
Guidance.pdf`; that source PDF is not present in this repository.

## Requested bug fixes

- [x] Make `--mp3-only` genuinely toggleable in single and batch runs.
  - Batch workers receive one explicit boolean flag.
  - Regression coverage verifies both the worker command and MP3-only versus
    MP3+WAV manifest/disk artifacts.
- [x] Fail fast when output already exists and `--force` is not set.
- [x] Remove duplicate chapter loading from `--list-chapters` flow.

## Architectural outcome

The refactor established the normalized source-ingestion layer and thin
compatibility facades. Those accepted decisions now live in
[../ARCHITECTURE.md](../ARCHITECTURE.md). Remaining scheduler and model-boundary
work is split into backlog items with explicit acceptance criteria.
