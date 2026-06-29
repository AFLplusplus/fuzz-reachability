#!/usr/bin/env bash
# Usage: gen_dangerous_list.sh <input.txt> <output.inc>
set -euo pipefail

in="$1"
out="$2"
tmp="$out.tmp"

awk '
  { line = $0
    sub(/#.*/, "", line)
    gsub(/^[ \t]+|[ \t]+$/, "", line) }
  line == "" { next }
  {
    if (substr(line, length(line)) == "*")
      printf "{\"%s\", true},\n", substr(line, 1, length(line) - 1)
    else
      printf "{\"%s\", false},\n", line
  }
' "$in" > "$tmp"

mv -f "$tmp" "$out"
