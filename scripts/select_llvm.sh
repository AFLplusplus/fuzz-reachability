#!/usr/bin/env bash
# Print the LLVM major, or its llvm-config path with --config. Select the newest
# installed major >= MIN_LLVM (21). Newer LLVM tools read older bitcode but not newer, so the
# newest toolchain is the safest default -- in particular it can read rustc's
# bitcode (a too-old LLVM cannot). Falls back to an unversioned llvm-config on
# PATH, then to Homebrew's keg-only LLVM (<prefix>/opt/llvm@N and opt/llvm,
# prefix from $HOMEBREW_PREFIX or the `brew` on PATH).
#
# Written for bash 3.2, the /bin/bash of macOS: no associative arrays, mapfile
# or readlink -f.
set -uo pipefail
MIN_LLVM="${MIN_LLVM:-21}"
output="${1:-}"
requested="${2:-}"

newest=""
newest_cfg=""

# Consider one llvm-config: keep it if it is the newest acceptable major so far.
consider() {
  local cfg="$1" m
  m="$("$cfg" --version 2>/dev/null | cut -d. -f1)"
  [[ "$m" =~ ^[0-9]+$ ]] || return 0
  [ "$m" -ge "$MIN_LLVM" ] || return 0
  [ -z "$requested" ] || [ "$m" = "$requested" ] || return 0
  if [ -z "$newest" ] || [ "$m" -gt "$newest" ]; then
    newest="$m"
    newest_cfg="$cfg"
  fi
}

# 1. versioned llvm-config-N on PATH, newest major wins.
IFS=: read -r -a path_dirs <<< "$PATH"
for directory in "${path_dirs[@]}"; do
  [ -d "$directory" ] || continue
  for cfg in "$directory"/llvm-config-*; do
    [ -x "$cfg" ] || continue
    consider "$cfg"
  done
done

# 2. an unversioned llvm-config on PATH.
if [ -z "$newest" ] && command -v llvm-config >/dev/null 2>&1; then
  consider "$(command -v llvm-config)"
fi

# 3. Homebrew's LLVM kegs, which are not linked onto PATH.
if [ -z "$newest" ]; then
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
fi

if [ -n "$newest" ]; then
  if [ "$output" = --config ]; then
    echo "$newest_cfg"
  else
    echo "$newest"
  fi
else
  echo "error: no llvm-config with LLVM >= $MIN_LLVM found on PATH or in Homebrew (brew install llvm)" >&2
  exit 1
fi
