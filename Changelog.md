### v1.1-dev
- JSON report: a new `summary.external_declarations` count and top-level
  `external_declarations` array (sorted mangled names) list reachable
  functions with no body in the analyzed bitcode — precompiled libs, Rust std
  without `--build-std`, asm units — the allowlist blind spot no static
  analysis can see into.
- README: new "Allowlist vs ignorelist — which is safe when bitcode is
  incomplete" section explains that a reachable function with no bitcode body
  is silently missing from `reached.txt` (allowlist blind spot) but absent
  from `not_reached.txt`, so the ignorelist still instruments it — as long as
  the coverage build actually recompiles that function through the coverage
  pass (precompiled libs and asm-only units are never recompiled, so neither
  list recovers them). Recommends the ignorelist as the conservative default;
  notes that `--build-std` / `--static-libs auto` / LTO-free builds shrink but
  do not eliminate the external set (precompiled libraries and asm remain
  inherent limits) and points at `summary.external_declarations` to quantify
  the gap.
- JSON report: each function now carries a build-independent `key` (mangled
  name minus the Rust disambiguator) so coverage tools join Rust generic
  instances across builds.
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
  match what `llvm-cov` reports. rustc does not guarantee the `17h<hash>`
  mangled-name disambiguator is stable across builds, so it is
  `reachability.json`'s build-independent `key` (the disambiguator stripped;
  see [Output](README.md#output)), not the raw mangled name, that guarantees
  the set lines up with the optimized fuzz/coverage binary;
  `driver/tests/test_rust_hash_stability.py` checks that `key` stays identical
  across opt levels.
  Native runs clean their throwaway opt-0 build afterward (`cargo ziggy clean`
  / `cargo afl clean` / `cargo clean` in `fuzz/`). `--optimize` restores the
  optimized/post-inline build for any language.
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
- The toolchain check no longer requires `opt` on `PATH`: it was only
  version-probed as a redundant proxy, so `clang`/`clang++`/`llvm-link` (the
  actual bitcode producer/merger) now define coherence.
- Rust acquisition warns when `deps/` holds bitcode from more than one build of a
  crate (the newest is chosen by mtime, which can be stale if that crate was
  cached); re-run with `--clean` for a fresh build.
- C/C++ artifact detection now recognizes fat/universal Mach-O binaries.

### v1.0
- initial release
