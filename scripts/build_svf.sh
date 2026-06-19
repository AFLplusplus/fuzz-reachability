#!/usr/bin/env bash
# Build SVF against a chosen SYSTEM LLVM major (preserving version coherence)
# plus a downloaded Z3 prebuilt. Produces a per-version install tree.
#
#   scripts/build_svf.sh [LLVM_MAJOR]      (default 21)
#
# Output: third_party/SVF/install-<major>/  (libSvfCore.a, libSvfLLVM.a, extapi.bc)
#
# SVF master targets LLVM 21.1.x. Building against newer majors (22, 23, ...) may
# fail on LLVM API changes -- that is expected and is exactly what the version
# test matrix (scripts/test_matrix.sh) detects. A failure here is non-fatal to
# the project: the type-based backend works without SVF (see docs/llvm-support.md).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TP="$ROOT/third_party"
SVF_DIR="$TP/SVF"
Z3_DIR="$TP/z3.obj"

MAJOR="${1:-21}"
LLVM_DIR="${LLVM_DIR:-/usr/lib/llvm-$MAJOR/lib/cmake/llvm}"
BUILD_DIR="$SVF_DIR/build-$MAJOR"
INSTALL_DIR="$SVF_DIR/install-$MAJOR"
# Pinned SVF master commit that targets LLVM 21.1.x.
SVF_COMMIT="${SVF_COMMIT:-795fd5cbbc2e5e343277c391300ba1d1d9903a73}"
JOBS="${JOBS:-4}"

if [ ! -d "$LLVM_DIR" ]; then
  echo "[svf] LLVM $MAJOR cmake dir not found: $LLVM_DIR" >&2
  exit 1
fi

mkdir -p "$TP"

# --- Z3 prebuilt (4.8.8, shared across all LLVM versions) ---
if [ ! -d "$Z3_DIR" ]; then
  echo "[svf] downloading Z3 4.8.8 prebuilt..."
  curl -fL "https://github.com/Z3Prover/z3/releases/download/z3-4.8.8/z3-4.8.8-x64-ubuntu-16.04.zip" -o "$TP/z3.zip"
  ( cd "$TP" && unzip -q z3.zip && mv z3-4.8.8-x64-ubuntu-16.04 z3.obj && rm -f z3.zip )
fi

# --- SVF source ---
if [ ! -d "$SVF_DIR/.git" ]; then
  echo "[svf] cloning SVF..."
  git clone https://github.com/SVF-tools/SVF.git "$SVF_DIR"
fi
( cd "$SVF_DIR" && git checkout -q "$SVF_COMMIT" 2>/dev/null \
    || { git fetch --depth 1 origin "$SVF_COMMIT" && git checkout -q "$SVF_COMMIT"; } )

# --- configure + build against the chosen LLVM + downloaded Z3 ---
echo "[svf] building against LLVM $MAJOR ($LLVM_DIR) -> $INSTALL_DIR"
# Drop a stale CMake cache (e.g. a build dir that was moved/renamed): cmake
# records its own absolute path and refuses to reconfigure from a new location.
if [ -f "$BUILD_DIR/CMakeCache.txt" ] && \
   ! grep -q "CMAKE_CACHEFILE_DIR:INTERNAL=$BUILD_DIR\$" "$BUILD_DIR/CMakeCache.txt"; then
  echo "[svf] stale CMake cache in $BUILD_DIR -- removing for a clean configure"
  rm -rf "$BUILD_DIR"
fi
export Z3_DIR
cmake -S "$SVF_DIR" -B "$BUILD_DIR" \
  -DCMAKE_BUILD_TYPE=Release \
  -DLLVM_DIR="$LLVM_DIR" \
  -DZ3_DIR="$Z3_DIR" \
  -DCMAKE_INSTALL_PREFIX="$INSTALL_DIR" \
  -DSVF_WARN_AS_ERROR=OFF \
  -DCMAKE_CXX_FLAGS="${CMAKE_CXX_FLAGS:-} -Wno-error"
cmake --build "$BUILD_DIR" -j"$JOBS"
cmake --install "$BUILD_DIR"
echo "[svf] OK: LLVM $MAJOR -> $INSTALL_DIR"
