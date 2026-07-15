### v1.1-dev
- Analyzer soundness: callback value-flow now follows every indexed store into
  globals, aggregates, stack objects, loads, and heap-returned objects; alias and
  pointer-cast chains use one cycle-safe callable resolver; callback-like operand
  bundles and defined personality functions contribute call-graph edges.
- Indirect resolution keeps exact function types as its precise path but unions
  address-flow-proven type-punned targets and conservatively widens unresolved
  cast provenance. Address-taken declarations use the same policy and appear as
  reached opaque leaves in `external_declarations` without changing defined-body
  metrics.
- New default-off `--include-process-lifecycle-roots` option adds constructors,
  destructors, ifunc resolvers, and a defined `LLVMFuzzerInitialize` as roots.
  Selected roots appear in `entries`; malformed records and unresolved explicit
  entries are always visible as warnings.
- Confidence evidence is now entry-relative. Unreachable code can no longer
  raise an indirectly reached decoy from `low` to `medium` confidence.
- Analyzer robustness: invalid-UTF-8 symbols are replaced safely in JSON/DOT,
  the LLVM debug-info option lookup is type-checked, and deterministic DOT output
  contains the same reachable defined-function subgraph as JSON edges.
- Output destinations are file-only, collision-checked through symlinks, and
  validated before cleanup. JSON, lists, and optional DOT are staged beside
  their destinations and published transactionally; failed or concurrent runs
  cannot expose mixed output sets or share extraction/link intermediates.
- `--clean` no longer recursively deletes project-wide `*.o`, `*.bc`, or
  manifests. It removes selected output files and C/C++ artifacts recorded in
  `.reachability-cache/owned-c-artifacts.json`, while retaining unrelated files.
- An explicit `--artifact` is now a strict contract: missing, unsupported, or
  unextractable paths fail rather than triggering discovery. Automatic discovery
  has no first-eight limit and rejects equally plausible candidates with a
  ranked list.
- Static archive expansion fails closed if any requested archive cannot be
  listed or fully extracted. Successful expansion work is retained for
  diagnostics, member names containing spaces are parsed correctly, and no
  complete-looking partial report is published.
- gllvm builds and every `get-bc` invocation now use per-run stable symlinks to
  the exact checked `clang`, `clang++`, and `llvm-link`; `check-toolchain` reports
  those effective tools.
- Rust acquisition uses Cargo metadata for workspace-root profile semantics,
  hierarchically merges Cargo configuration, honors `CARGO_TARGET_DIR`, filters
  host-only artifacts consistently, and restricts fallback collection to the
  current invocation. Multiple live versions and every codegen unit of the same
  crate are preserved instead of collapsing by crate name.
- Expected subprocess, decoding, JSON, and I/O failures now become concise
  stage-specific CLI errors without tracebacks. Analyzer warnings are forwarded
  on every run, while clean runs stay quiet.
- Large bitcode sets are linked in bounded batches, Cargo artifact output and
  non-verbose C/C++ build logs are spooled, and the analyzer writes staged JSON
  directly to its final parser input rather than round-tripping a second copy.
- Hosted CI covers LLVM 21, 22, and 23 plus full C/Rust CLI paths and reproducible
  cppcheck/Clang Static Analyzer gates. Matrix discovery follows `PATH` and fails
  without a supported LLVM unless an explicit local skip is requested.
- Development setup pins gllvm 1.3.1, pytest 9.1.1, and setuptools 80.9.0;
  `make compdb` generates a clangd compilation database and
  `make static-analysis` reproduces the analyzer checks.
- New `--mangling {auto,legacy,v0}` flag (default `auto`) on `reachability run`,
  for every Rust `--lang` value: forces the analysis build's rustc
  `-Csymbol-mangling-version` so its Rust symbols match whatever build you join
  this report against. A `-Cinstrument-coverage` coverage build is always v0
  regardless of the crate's own default, so `--mangling v0` makes the analysis
  bitcode's mangled Rust names match it — byte-identical for every case measured
  so far (crate-local generics) — closing the `key`/exact-name join gap the v0
  mangling limitation previously described, with no disambiguator normalization
  needed. A v0 disambiguator that drifted between two v0 builds (an untested
  cross-crate/`-Zshare-generics` case) would fall back to cov-analysis's
  `(file,line)` join. `auto` (the default) appends nothing, so
  cargo-afl/ziggy/cargo-fuzz builds (legacy by default) are unaffected. The JSON
  report gained a top-level `mangling` field (`"legacy"`/`"v0"`) reporting which
  scheme the analyzed bitcode actually used.
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
- C/C++ artifact detection now recognizes fat/universal Mach-O binaries.

### v1.0
- initial release
