#!/usr/bin/env bash
# One-shot environment setup for the fuzz-reachability analyzer (Makefile build).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Which LLVM to build the analyzer against (>= 21). Default: the newest installed
# major, since newer LLVM tools can read older bitcode (including rustc's) but not
# newer -- see select_llvm.sh.
LLVM_MAJOR="${LLVM_MAJOR:-$(bash "$ROOT/scripts/select_llvm.sh")}"
LLVM_CONFIG="llvm-config-${LLVM_MAJOR}"

# 1. gllvm (C/C++ whole-program bitcode extraction) via Go.
if ! command -v gclang >/dev/null 2>&1; then
  if ! command -v go >/dev/null 2>&1; then
    echo "error: Go is required to install gllvm; install go and re-run setup" >&2
    exit 1
  fi
  echo "[setup] installing gllvm via go install..."
  go install github.com/SRI-CSL/gllvm/cmd/...@v1.3.1
  echo "[setup] ensure $(go env GOPATH)/bin is on your PATH"
fi

# 2. rust-src component (only needed for the optional --build-std path).
echo "[setup] adding rust-src component (for --build-std)..."
rustup component add rust-src || true

# 3. create the Python venv with the driver + test deps.
echo "[setup] creating .venv..."
bash "$ROOT/scripts/setup_venv.sh"

# 4. build the analyzer against the chosen LLVM (no CMake).
echo "[setup] building analyzer against LLVM ${LLVM_MAJOR}..."
make -C "$ROOT/analyzer" LLVM_CONFIG="${LLVM_CONFIG}"

echo "[setup] done."
echo "[setup] analyzer: $ROOT/analyzer/build/reachability-analyzer"
echo "[setup] export REACHABILITY_ANALYZER=$ROOT/analyzer/build/reachability-analyzer"
