#!/usr/bin/env bash
# LLVM version-compatibility matrix: build + test the analyzer against every
# installed llvm-config-NN with NN >= MIN_LLVM. Prints a matrix and exits
# non-zero if any build/test fails -- this is the early-warning system for
# breakage on future LLVMs.
#
#   scripts/test_matrix.sh
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MIN_LLVM=21
PY="$ROOT/.venv/bin/python"
PATH="$(go env GOPATH 2>/dev/null)/bin:$PATH"  # gllvm, if installed
export PATH

declare -A CORE
declare -A CONFIG
versions=()
overall=0

declare -A seen
configs=()
IFS=: read -r -a path_dirs <<< "$PATH"
for directory in "${path_dirs[@]}"; do
  [ -d "$directory" ] || continue
  for cfg in "$directory"/llvm-config-*; do
    [ -x "$cfg" ] || continue
    resolved="$(readlink -f "$cfg" 2>/dev/null || printf '%s' "$cfg")"
    [ -z "${seen[$resolved]:-}" ] || continue
    seen[$resolved]=1
    configs+=("$resolved")
  done
done
while IFS= read -r cfg; do
  [ -n "$cfg" ] || continue
  major="$($cfg --version 2>/dev/null | cut -d. -f1)"
  [[ "$major" =~ ^[0-9]+$ ]] || continue
  [ "$major" -ge "$MIN_LLVM" ] || continue
  CONFIG[$major]="$cfg"
done < <(printf '%s\n' "${configs[@]}" | sort -V)

if [ "${#CONFIG[@]}" -eq 0 ]; then
  if [ "${MATRIX_ALLOW_EMPTY:-0}" = 1 ]; then
    echo "matrix: SKIP (no llvm-config with LLVM >= $MIN_LLVM found on PATH)"
    exit 0
  fi
  echo "matrix: FAIL (no llvm-config with LLVM >= $MIN_LLVM found on PATH)" >&2
  exit 1
fi

mapfile -t versions < <(printf '%s\n' "${!CONFIG[@]}" | sort -n)
[ -x "$PY" ] || bash "$ROOT/scripts/setup_venv.sh"

for major in "${versions[@]}"; do
  cfg="${CONFIG[$major]}"
  echo "=== LLVM $major ==="

  if make -C "$ROOT/analyzer" LLVM_CONFIG="$cfg" BUILD="build/$major" \
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
done

echo
printf "%-8s %-12s\n" "LLVM" "result"
printf "%-8s %-12s\n" "----" "------"
for v in "${versions[@]}"; do
  printf "%-8s %-12s\n" "$v" "${CORE[$v]}"
done
echo
[ "$overall" -eq 0 ] && echo "matrix: all PASS" || echo "matrix: FAILURES (see /tmp/matrix-*.log)"
exit "$overall"
