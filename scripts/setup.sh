#!/usr/bin/env bash
set -euo pipefail

find_uv() {
  if command -v uv >/dev/null 2>&1; then
    command -v uv
    return 0
  fi
  if [ -x "$HOME/.local/bin/uv" ]; then
    printf '%s\n' "$HOME/.local/bin/uv"
    return 0
  fi
  local snap_uv
  snap_uv="$(find "$HOME/snap/code" -path '*/.local/bin/uv' -type f -executable 2>/dev/null | sort -V | tail -n 1 || true)"
  if [ -n "$snap_uv" ]; then
    printf '%s\n' "$snap_uv"
    return 0
  fi
  return 1
}

install_uv() {
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    return 0
  fi
  if command -v wget >/dev/null 2>&1; then
    wget -qO- https://astral.sh/uv/install.sh | sh
    return 0
  fi
  return 1
}

install_python_requirements() {
  local uv_bin
  uv_bin="$(find_uv || true)"
  if [ -n "$uv_bin" ]; then
    "$uv_bin" pip install --python ./.venv/bin/python -r requirements.txt
    if [ "${1:-}" = "--dev" ]; then
      "$uv_bin" pip install --python ./.venv/bin/python -r requirements-dev.txt
    fi
    return 0
  fi
  ./.venv/bin/python -m pip install --upgrade pip
  ./.venv/bin/python -m pip install -r requirements.txt
  if [ "${1:-}" = "--dev" ]; then
    ./.venv/bin/python -m pip install -r requirements-dev.txt
  fi
}

PYTHON_BIN="${PYTHON_BIN:-}"
if [ ! -d ".venv" ]; then
  if [ -n "$PYTHON_BIN" ]; then
    "$PYTHON_BIN" -m venv .venv
  elif command -v python3.12 >/dev/null 2>&1; then
    python3.12 -m venv .venv
  else
    UV_BIN="$(find_uv || true)"
    if [ -z "$UV_BIN" ]; then
      install_uv
      UV_BIN="$(find_uv || true)"
    fi
    if [ -z "$UV_BIN" ]; then
      echo "Could not create .venv. Install python3.12-venv or uv, then rerun setup." >&2
      exit 2
    fi
    "$UV_BIN" python install 3.12
    "$UV_BIN" venv --python 3.12 .venv
  fi
fi
install_python_requirements "${1:-}"
