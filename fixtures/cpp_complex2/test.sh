#!/bin/bash

test -z "$AFL_PATH" && AFL_PATH=/prg/dev
PATH=$AFL_PATH:$PATH

AFL_LLVM_ABORTLIST=1 AFL_LLVM_ALLOWLIST=`pwd`/reached.txt \
  afl-clang-fast++ main.cpp -o main -fsanitize=fuzzer -O0 -fno-inline -fcoroutines -std=c++20 -Wno-unused-command-line-argument 

mkdir -p in
echo > in/in
AFL_BENCH_UNTIL_CRASH=1 afl-fuzz -i in -o out -V 300 -- ./main
