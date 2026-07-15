#!/usr/bin/env bash
# Print the LLVM major to build the analyzer against: the newest installed major
# >= MIN_LLVM (21). Newer LLVM tools read older bitcode but not newer, so the
# newest toolchain is the safest default -- in particular it can read rustc's
# bitcode (a too-old LLVM cannot). Fails when no suitable llvm-config is found.
set -uo pipefail
MIN_LLVM="${MIN_LLVM:-21}"

newest=""
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
  m="$($cfg --version 2>/dev/null | cut -d. -f1)"
  [[ "$m" =~ ^[0-9]+$ ]] || continue
  [ "$m" -ge "$MIN_LLVM" ] || continue
  if [ -z "$newest" ] || [ "$m" -gt "$newest" ]; then
    newest="$m"
  fi
done < <(printf '%s\n' "${configs[@]}" | sort -V)

if [ -n "$newest" ]; then
  echo "$newest"
else
  echo "error: no llvm-config with LLVM >= $MIN_LLVM found on PATH" >&2
  exit 1
fi
