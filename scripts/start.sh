#!/usr/bin/env bash
set -euo pipefail
./.venv/bin/python ./md_to_audio.py "$@"
