# Requested Changes

## Review items from `Local Tts Renderer Review And Refactor Guidance.pdf`

- [x] Fix `--mp3-only` semantics so it is actually toggleable in single-run and batch CLI.
- [x] Make overwrite behavior fail fast when outputs already exist and `--force` is not set.
- [x] Remove duplicate chapter loading in `--list-chapters` output flow.
- [ ] Track remaining architectural guidance from the PDF separately from the bug fixes above.

## Notes

- This file tracks only the review items explicitly requested for this pass.
- Remaining domain refactor guidance should be split into smaller follow-up tasks before implementation.
