#!/usr/bin/env bash
# LLVM version-compatibility matrix: build + test the analyzer against every
# installed llvm-config-NN with NN >= MIN_LLVM, for both the core (type-based)
# backend and SVF. Prints a matrix and exits non-zero if any CORE build/test
# fails -- this is the early-warning system for breakage on future LLVMs.
#
# SVF is optional: an SVF that does not build against a given LLVM is reported,
# not treated as a failure (the type-based backend is the supported baseline).
#
#   scripts/test_matrix.sh              # core matrix + SVF where already built
#   BUILD_SVF=1 scripts/test_matrix.sh  # also attempt to build SVF per version
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MIN_LLVM=21
PY="$ROOT/.venv/bin/python"
PATH="$(go env GOPATH 2>/dev/null)/bin:$PATH"  # gllvm, if installed
export PATH

declare -A CORE SVFRES
versions=()
overall=0

for cfg in $(ls /usr/bin/llvm-config-* 2>/dev/null | sort -V); do
  major="$($cfg --version 2>/dev/null | cut -d. -f1)"
  [[ "$major" =~ ^[0-9]+$ ]] || continue
  [ "$major" -ge "$MIN_LLVM" ] || continue
  versions+=("$major")
  echo "=== LLVM $major ==="

  # --- core build + analyzer behavior tests (no external toolchain needed) ---
  if make -C "$ROOT/analyzer" LLVM_CONFIG="llvm-config-$major" BUILD="build/$major" \
        >"/tmp/matrix-core-$major.log" 2>&1; then
    if REACHABILITY_ANALYZER="$ROOT/analyzer/build/$major/reachability-analyzer" \
       "$PY" -m pytest "$ROOT/driver/tests/test_analyzer_core.py" -q \
       -p no:cacheprovider >"/tmp/matrix-coretest-$major.log" 2>&1; then
      CORE[$major]="PASS"
    else
      CORE[$major]="TEST-FAIL"; overall=1
    fi
  else
    CORE[$major]="BUILD-FAIL"; overall=1
  fi

  # --- SVF (optional) ---
  svf_install="$ROOT/third_party/SVF/install-$major"
  if [ "${BUILD_SVF:-0}" = "1" ] && [ ! -f "$svf_install/lib/libSvfLLVM.a" ]; then
    JOBS="${JOBS:-4}" bash "$ROOT/scripts/build_svf.sh" "$major" \
        >"/tmp/matrix-svfbuild-$major.log" 2>&1 || true
  fi
  if [ -f "$svf_install/lib/libSvfLLVM.a" ]; then
    if make -C "$ROOT/analyzer" LLVM_CONFIG="llvm-config-$major" SVF=1 \
          BUILD="build/$major-svf" >"/tmp/matrix-svfa-$major.log" 2>&1; then
      if REACHABILITY_ANALYZER_SVF="$ROOT/analyzer/build/$major-svf/reachability-analyzer" \
         "$PY" -m pytest \
         "$ROOT/driver/tests/test_analyzer_core.py::test_svf_backend_sound" -q \
         -p no:cacheprovider >"/tmp/matrix-svft-$major.log" 2>&1; then
        SVFRES[$major]="PASS"
      else
        SVFRES[$major]="TEST-FAIL"
      fi
    else
      SVFRES[$major]="ANALYZER-LINK-FAIL"
    fi
  else
    SVFRES[$major]="not built (SVF source targets 21.1.x; see docs/llvm-support.md)"
  fi
done

echo
printf "%-8s %-12s %-50s\n" "LLVM" "core" "svf"
printf "%-8s %-12s %-50s\n" "----" "----" "---"
for v in "${versions[@]}"; do
  printf "%-8s %-12s %-50s\n" "$v" "${CORE[$v]}" "${SVFRES[$v]}"
done
echo
[ "$overall" -eq 0 ] && echo "CORE matrix: all PASS" || echo "CORE matrix: FAILURES (see /tmp/matrix-*.log)"
exit "$overall"
