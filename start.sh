#!/usr/bin/env bash
# Launch Video Transcriber on macOS / Linux. First run sets everything up.
set -euo pipefail
cd "$(dirname "$0")"

PY=$(command -v python3 || command -v python || true)
if [ -z "$PY" ]; then
  echo "Python 3.10+ is required: https://www.python.org/downloads/" >&2
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "Setting up for the first time. This takes a few minutes..."
  "$PY" -m venv .venv
  .venv/bin/python -m pip install --upgrade pip >/dev/null
  .venv/bin/python -m pip install -r requirements.txt
  if command -v nvidia-smi >/dev/null 2>&1; then
    echo "NVIDIA GPU detected, installing GPU acceleration libraries..."
    .venv/bin/python -m pip install -r requirements-gpu.txt || true
  fi
fi

exec .venv/bin/python -m transcriber "$@"
