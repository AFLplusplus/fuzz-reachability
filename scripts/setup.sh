#!/usr/bin/env bash
# One-shot environment setup for the fuzz-reachability analyzer (Makefile build).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Which LLVM to build the analyzer against (>= 21). Default: the newest installed
# major, since newer LLVM tools can read older bitcode (including rustc's) but not
# newer -- see select_llvm.sh.
LLVM_CONFIG="${LLVM_CONFIG:-$(bash "$ROOT/scripts/select_llvm.sh" --config "${LLVM_MAJOR:-}")}"
LLVM_MAJOR="$("$LLVM_CONFIG" --version | cut -d. -f1)"

# 1. The bitcode capture `reachability run` uses by default: gllvm via Go, or
#    with REACHABILITY_CAPTURE=rllvm, rllvm via Homebrew or else cargo.
RLLVM_MIN=0.7.1
rllvm_ok() {
  local have
  have="$(rllvm-cc --rllvm-version 2>/dev/null | awk '{print $2}')" || return 1
  [ -n "$have" ] \
    && [ "$(printf '%s\n' "$RLLVM_MIN" "$have" | sort -V | head -n 1)" = "$RLLVM_MIN" ]
}
if [ "${REACHABILITY_CAPTURE:-gllvm}" = rllvm ]; then
  if ! rllvm_ok; then
    if command -v brew >/dev/null 2>&1; then
      echo "[setup] installing rllvm via brew install..."
      brew install h1994st/tap/rllvm
    elif command -v cargo >/dev/null 2>&1; then
      echo "[setup] installing rllvm >= ${RLLVM_MIN} via cargo install..."
      cargo install --locked rllvm --version ">=${RLLVM_MIN}"
    else
      echo "error: Homebrew or cargo is required to install rllvm" >&2
      exit 1
    fi
    if ! rllvm_ok; then
      echo "warning: the rllvm-cc on PATH is still older than ${RLLVM_MIN}" >&2
    fi
  fi
elif ! command -v gclang >/dev/null 2>&1; then
  if ! command -v go >/dev/null 2>&1; then
    echo "error: Go is required to install gllvm; install go and re-run setup" >&2
    exit 1
  fi
  echo "[setup] installing gllvm via go install..."
  go install github.com/SRI-CSL/gllvm/cmd/...@v1.3.1
  echo "[setup] ensure $(go env GOPATH)/bin is on your PATH"
fi

# 2. rust-src component (only needed for the optional --build-std path).
if command -v rustup >/dev/null 2>&1; then
  echo "[setup] adding rust-src component (for --build-std)..."
  rustup component add rust-src || true
else
  echo "[setup] rustup unavailable; skipping optional rust-src installation"
fi

# 3. create the Python venv with the driver + test deps.
echo "[setup] creating .venv..."
bash "$ROOT/scripts/setup_venv.sh"

# 4. build the analyzer against the chosen LLVM (no CMake).
echo "[setup] building analyzer against LLVM ${LLVM_MAJOR}..."
make -C "$ROOT/analyzer" LLVM_CONFIG="${LLVM_CONFIG}"

echo "[setup] done."
echo "[setup] analyzer: $ROOT/analyzer/build/reachability-analyzer"
echo "[setup] export REACHABILITY_ANALYZER=$ROOT/analyzer/build/reachability-analyzer"
