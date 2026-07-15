#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
reachability="${REACHABILITY:-$root/.venv/bin/reachability}"
"$reachability" run --project . --lang cpp --artifact main.o
command -v afl-clang-fast++ >/dev/null 2>&1 || exit 0
command -v afl-fuzz >/dev/null 2>&1 || exit 0
AFL_LLVM_ABORTLIST=1 AFL_LLVM_ALLOWLIST="$PWD/reached.txt" \
  afl-clang-fast++ main.cpp -o main -fsanitize=fuzzer -O0 -fno-inline \
  -fcoroutines -std=c++20 -Wno-unused-command-line-argument
mkdir -p in
printf '\n' > in/in
unset AFL_NO_CRASH_README
AFL_NO_UI=1 AFL_BENCH_UNTIL_CRASH=1 afl-fuzz -i in -o out -V 30 -- ./main \
  >/dev/null 2>&1
test ! -s out/default/crashes/README.txt
make clean >/dev/null 2>&1
