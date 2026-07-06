### v1.1-dev
- C/C++ reachability is now **source-faithful by default**: the analysis build
  emits bitcode with `-fno-inline -fno-inline-functions` (via gllvm's
  `LLVM_BITCODE_GENERATION_FLAGS`, applied only to the analyzed bitcode, not the
  native object), so functions that the optimizer would inline still appear in
  `reachability.json`/`reached.txt`. This matches what `llvm-cov` reports (for
  coverage analysis) and remains a safe allowlist superset (instrumentation is
  applied post-inline, so extra names are no-ops). The new `--optimize` flag
  restores the previous optimized/post-inline behavior.
- Rust reachability is now source-faithful by default too: the analysis build
  forces `-Copt-level=0` (plain `--lang rust`/`mixed` via composed RUSTFLAGS;
  native `libfuzzer`/`ziggy`/`afl` via the RUSTC wrapper), so functions the
  optimizer would inline still appear in `reachability.json`/`reached.txt` and
  match what `llvm-cov` reports. Mangled-name hashes are opt-level-independent,
  so the set still matches the optimized fuzz/coverage binary. Native runs clean
  their throwaway opt-0 build afterward (`cargo ziggy clean` / `cargo afl clean`
  / `cargo clean` in `fuzz/`). `--optimize` restores the optimized/post-inline
  build for any language.
- JSON report: each reachable function now carries a `depth` (fewest call-graph
  hops from the nearest entry; entries are `0`), and a top-level `edges` array
  gives the reachable call graph as `{from, to, kind}`.
- JSON report: each reachable function now carries per-function triage metrics —
  `basic_blocks`, `cyclomatic`, `loops`, `dangerous_calls`, `C11` (local variable
  count), `interesting` (pointer-argument path from an entry), `bottleneck`
  (call-graph dominator), and `dead_end` (calls no `interesting` function). See
  the "Function metrics" section of the README.
- The `dangerous_calls` function list is now the editable `dangerous_functions.txt`
  at the project root, compiled into the analyzer at build time.
- C/C++ acquisition: auto-detected builds now also disable link-time
  optimization (`-DCMAKE_INTERPROCEDURAL_OPTIMIZATION=OFF` / `-Db_lto=false` /
  probed `--disable-lto`), because gllvm cannot extract bitcode from an `-flto`
  build. When extraction still fails, the error now names the likely cause and
  fix (LTO, an afl-clang-fast/clang-LTO binary with an empty `.llvm_bc`, a
  ccache/sccache layer, or assembly-only units) via a new `diagnostics` module.

### v1.0
- initial release
