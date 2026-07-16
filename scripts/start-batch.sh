#!/usr/bin/env bash
set -euo pipefail
model_free_command=0
for argument in "$@"; do
  case "$argument" in
    -h|--help)
      model_free_command=1
      break
      ;;
  esac
done
if [[ "$model_free_command" == "0" ]]; then
  ./.venv/bin/python ./scripts/bootstrap_models.py "$@"
  if [[ "${SKIP_DOCTOR:-0}" != "1" ]]; then
    ./.venv/bin/python ./scripts/doctor.py "$@"
  fi
fi
./.venv/bin/python ./run_tts_batch.py "$@"
