#!/usr/bin/env bash
# LLVM version-compatibility matrix: build + test the analyzer against every
# installed llvm-config-NN with NN >= MIN_LLVM, or an unversioned llvm-config
# if none is found, plus each Homebrew LLVM keg. Prints a matrix and exits
# non-zero if any build/test fails.
#
#   scripts/test_matrix.sh
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MIN_LLVM=21
PY="$ROOT/.venv/bin/python"
PATH="$(go env GOPATH 2>/dev/null)/bin:$PATH"  # gllvm, if installed
export PATH

# bash 3.2 (macOS /bin/bash) has no associative arrays: parallel indexed arrays
# map each major to its llvm-config and its result.
majors=()
configs=()
results=()
overall=0

# Record `cfg` under its major unless that major already has one.
consider() {
  local cfg="$1" major m
  major="$("$cfg" --version 2>/dev/null | cut -d. -f1)"
  [[ "$major" =~ ^[0-9]+$ ]] || return 0
  [ "$major" -ge "$MIN_LLVM" ] || return 0
  for m in ${majors[@]+"${majors[@]}"}; do
    [ "$m" != "$major" ] || return 0
  done
  majors+=("$major")
  configs+=("$cfg")
}

IFS=: read -r -a path_dirs <<< "$PATH"
for directory in "${path_dirs[@]}"; do
  [ -d "$directory" ] || continue
  for cfg in "$directory"/llvm-config-*; do
    [ -x "$cfg" ] || continue
    consider "$cfg"
  done
done

if [ "${#majors[@]}" -eq 0 ] && command -v llvm-config >/dev/null 2>&1; then
  consider "$(command -v llvm-config)"
fi

# Homebrew's keg-only LLVMs (see select_llvm.sh).
prefixes=()
[ -z "${HOMEBREW_PREFIX:-}" ] || prefixes+=("$HOMEBREW_PREFIX")
if brew="$(command -v brew 2>/dev/null)"; then
  prefixes+=("$(cd "$(dirname "$brew")/.." && pwd -P)")
fi
for prefix in ${prefixes[@]+"${prefixes[@]}"}; do
  for cfg in "$prefix"/opt/llvm@*/bin/llvm-config "$prefix"/opt/llvm/bin/llvm-config; do
    [ -x "$cfg" ] || continue
    consider "$cfg"
  done
done

if [ "${#majors[@]}" -eq 0 ]; then
  if [ "${MATRIX_ALLOW_EMPTY:-0}" = 1 ]; then
    echo "matrix: SKIP (no llvm-config with LLVM >= $MIN_LLVM found on PATH or in Homebrew)"
    exit 0
  fi
  echo "matrix: FAIL (no llvm-config with LLVM >= $MIN_LLVM found on PATH or in Homebrew)" >&2
  exit 1
fi

# Indices into majors/configs, in ascending major order.
order=()
while IFS=' ' read -r _ index; do
  order+=("$index")
done < <(for i in "${!majors[@]}"; do echo "${majors[$i]} $i"; done | sort -n)
[ -x "$PY" ] || bash "$ROOT/scripts/setup_venv.sh"

for i in "${order[@]}"; do
  major="${majors[$i]}"
  cfg="${configs[$i]}"
  echo "=== LLVM $major ==="

  if make -C "$ROOT/analyzer" LLVM_CONFIG="$cfg" BUILD="build/$major" \
        >"/tmp/matrix-core-$major.log" 2>&1; then
    if REACHABILITY_ANALYZER="$ROOT/analyzer/build/$major/reachability-analyzer" \
       "$PY" -m pytest "$ROOT/driver/tests/test_analyzer_core.py" -q \
       -p no:cacheprovider >"/tmp/matrix-coretest-$major.log" 2>&1; then
      results[$i]="PASS"
    else
      results[$i]="TEST-FAIL"; overall=1
    fi
  else
    results[$i]="BUILD-FAIL"; overall=1
  fi
done

echo
printf "%-8s %-12s\n" "LLVM" "result"
printf "%-8s %-12s\n" "----" "------"
for i in "${order[@]}"; do
  printf "%-8s %-12s\n" "${majors[$i]}" "${results[$i]}"
done
echo
[ "$overall" -eq 0 ] && echo "matrix: all PASS" || echo "matrix: FAILURES (see /tmp/matrix-*.log)"
exit "$overall"
