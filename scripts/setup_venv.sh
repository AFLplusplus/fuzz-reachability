#!/usr/bin/env bash
# Create the project's Python virtualenv (.venv) with the driver installed
# (editable) plus its test dependencies. Idempotent: safe to re-run.
#
#   bash scripts/setup_venv.sh          # -> .venv at repo root
#   PYTHON=python3.12 bash scripts/setup_venv.sh
#   VENV=/some/where bash scripts/setup_venv.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${VENV:-$ROOT/.venv}"
PYTHON="${PYTHON:-python3}"

if [ ! -x "$VENV/bin/python" ]; then
  echo "[venv] creating $VENV (using $PYTHON)"
  "$PYTHON" -m venv "$VENV"
fi

echo "[venv] installing driver + test deps (editable)"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -e "$ROOT/driver[test]"

echo "[venv] ready: $VENV"
echo "[venv]   $VENV/bin/reachability check-toolchain"
echo "[venv]   make test"
