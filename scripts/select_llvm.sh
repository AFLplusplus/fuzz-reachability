#!/usr/bin/env bash
# Print the LLVM major, or its llvm-config path with --config. Select the newest
# installed major >= MIN_LLVM (21). Newer LLVM tools read older bitcode but not newer, so the
# newest toolchain is the safest default -- in particular it can read rustc's
# bitcode (a too-old LLVM cannot). Falls back to an unversioned llvm-config.
set -uo pipefail
MIN_LLVM="${MIN_LLVM:-21}"
output="${1:-}"
requested="${2:-}"

newest=""
newest_cfg=""
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
  m="$("$cfg" --version 2>/dev/null | cut -d. -f1)"
  [[ "$m" =~ ^[0-9]+$ ]] || continue
  [ "$m" -ge "$MIN_LLVM" ] || continue
  [ -z "$requested" ] || [ "$m" = "$requested" ] || continue
  if [ -z "$newest" ] || [ "$m" -gt "$newest" ]; then
    newest="$m"
    newest_cfg="$cfg"
  fi
done < <(printf '%s\n' "${configs[@]}" | sort -V)

if [ -z "$newest" ] && command -v llvm-config >/dev/null 2>&1; then
  cfg="$(command -v llvm-config)"
  m="$("$cfg" --version 2>/dev/null | cut -d. -f1)"
  if [[ "$m" =~ ^[0-9]+$ ]] && [ "$m" -ge "$MIN_LLVM" ] \
      && { [ -z "$requested" ] || [ "$m" = "$requested" ]; }; then
    newest="$m"
    newest_cfg="$cfg"
  fi
fi

if [ -n "$newest" ]; then
  if [ "$output" = --config ]; then
    echo "$newest_cfg"
  else
    echo "$newest"
  fi
else
  echo "error: no llvm-config with LLVM >= $MIN_LLVM found on PATH" >&2
  exit 1
fi
