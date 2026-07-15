#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
reachability="${REACHABILITY:-$root/.venv/bin/reachability}"
"$reachability" run --project . --lang ziggy
command -v afl-fuzz >/dev/null 2>&1 || exit 0
AFL_LLVM_ALLOWLIST="$PWD/reached.txt" cargo ziggy build --no-honggfuzz
mkdir -p in
printf '\n' > in/in
afl-fuzz -i in -o out -V 60 -- target/afl/debug/rust_ziggy_indirect_calls
