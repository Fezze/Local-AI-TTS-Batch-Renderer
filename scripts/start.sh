#!/usr/bin/env bash
set -euo pipefail
if [[ "${SKIP_DOCTOR:-0}" != "1" ]]; then
  ./.venv/bin/python ./scripts/doctor.py
fi
./.venv/bin/python ./md_to_audio.py "$@"
