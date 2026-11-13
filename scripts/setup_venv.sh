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

# The default python3 must be >= 3.10 (macOS ships 3.9 as /usr/bin/python3);
# otherwise use the newest python3.N >= 3.10 on PATH, e.g. Homebrew's.
py_ok() {
  "$1" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null
}
if [ -z "${PYTHON:-}" ]; then
  PYTHON=python3
  if ! py_ok python3; then
    for minor in $(seq 30 -1 10); do
      if command -v "python3.$minor" >/dev/null 2>&1 && py_ok "python3.$minor"; then
        PYTHON="python3.$minor"
        break
      fi
    done
  fi
fi
if ! py_ok "$PYTHON"; then
  echo "error: Python >= 3.10 is required ($PYTHON is older or missing);" \
       "install a newer one (macOS: brew install python) or set PYTHON" >&2
  exit 1
fi

if [ ! -x "$VENV/bin/python" ]; then
  echo "[venv] creating $VENV (using $PYTHON)"
  "$PYTHON" -m venv "$VENV"
fi

echo "[venv] installing driver + test deps (editable)"
"$VENV/bin/pip" install --quiet --constraint "$ROOT/requirements-dev.txt" \
  -e "$ROOT/driver[test]"

echo "[venv] ready: $VENV"
echo "[venv]   $VENV/bin/reachability check-toolchain"
echo "[venv]   make test"
