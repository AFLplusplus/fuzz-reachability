### v1.2-dev
- Support cargo 1.100's new build layout
- C/C++ analysis bitcode is now compiled at `-O0` in addition to
  `-fno-inline -fno-inline-functions` (bitcode compile only, via
  `LLVM_BITCODE_GENERATION_FLAGS`; the native object keeps the project's `-Ox`).
- New post-optimizer warning: when the analyzed bitcode was built with
  optimization and `--optimize` was not requested — a prebuilt `--artifact`, or a
  build that recompiled nothing. The run says so instead of silently reporting a
  function set that cannot be joined to coverage.
- Failure diagnosis recognizes a failed gllvm bitcode compile
  (`Failed to build bitcode file for …`), which `gclang` reports while still
  exiting zero, and names the injected flags and the escape hatch.
- `reachability check-toolchain` no longer fails on a host with no Rust toolchain
- `reachability run --no-name-roots` exposes the analyzer's flag of the same name,
  so the contribution of the runtime-name-lookup root heuristic can be measured
  without invoking the analyzer by hand.
- Documentation: the C/C++ optimization-independence claim is corrected and the
  prebuilt-bitcode trap documented; `Limitations` now covers method-table-heavy
  codebases where the over-approximation swallows the tree, with per-subsystem
  scoping and the `indirect_only`/`low_confidence` counts as the recovery.
- The driver now requires Python >= 3.10 instead of >= 3.12. On 3.10 the
  `tomli` backport supplies `tomllib`; it is not installed on 3.11+. CI runs the
  LLVM 21 job on Python 3.10.
- Soundness: a `static` function whose name collides with one in another
  translation unit is no longer dropped from instrumentation. llvm-link renames
  one copy (`helper` -> `helper.1`); the lists used that merged-module name, so
  the reachable copy matched nothing under the allowlist and was excluded by the
  other copy's name under the ignorelist. The analyzer now recovers the
  object-file name from debug info, reports it as `mangled` (with the merged
  name in the new `ir_name` field), lists reachable functions under both names,
  and keeps any name a reachable copy carries out of the ignorelist.
- Soundness: the analyzer promotes stack slots to SSA registers before building
  value flow. The `-O0` analysis default had stopped flow from connecting stores
  and loads through heap objects (every pointer is reloaded from its own stack
  slot), so callbacks whose signature differs from the call site were missed:
  on libxml2 the `-O0` run reached 1674 functions against 1902 from `-O2`
  bitcode; it now reaches 1901. Local-variable counts are taken before the
  promotion.
- Soundness: a callee that stores a function, or a pointer parameter it
  received, through one of its pointer parameters (setters such as
  `set_cb(obj, fn)`) now counts as storing it into the caller's object, so a
  callback whose signature differs from its call site is no longer missed when
  it is installed through a setter.
- Soundness: `LLVMFuzzerInitialize` is rooted whenever `LLVMFuzzerTestOneInput`
  is an entry, and callbacks that constructors hand to external code become
  roots without `--include-process-lifecycle-roots`. Exit-time registrations
  (`atexit`, `__cxa_atexit` static-object destructors, …) stay lifecycle roots.
- Soundness: storing a function into a global that is only declared in the
  bitcode (an error or allocator hook owned by a precompiled library), or into
  memory reached through one, counts as an escape.
- Performance: indirect call sites share one value-flow computation and point at
  shared target sets instead of carrying one edge per candidate. A synthetic
  8000-entry dispatch table used from 8000 sites went from 79 s / 8.4 GB to
  19 s / 0.1 GB, or 0.15 s with the new `--no-edges` (analyzer and driver),
  which omits the `edges` array whose size is inherently sites x targets.
- Confidence: a function stored into the same struct field, or the same C++ or
  Rust vtable slot, that a reached indirect call loads now rates `medium`.
  Previously every C++ virtual target rated `low`.
- `interesting` / `dead_end`: the pointer chain now also starts at closures and
  lambdas (including clang's `$_N` lambdas) defined inside an entry, so ziggy
  and cargo-afl harnesses (whose Rust `main` takes no pointer) no longer report
  every function as uninteresting.
- JSON schema version 2: new top-level `schema_version` and `run` (driver
  version and the options that shaped the analysis; analyzer `--run-info`),
  per-function `ir_name`, and `C11` renamed to `local_vars`.
- Auto-detected CMake and Meson builds configure into a tool-owned
  `build-reachability/` directory instead of the project's `build/`, which both
  tools would keep configured with the previous compiler. `--clean` removes it.
- `--optimize`, `--build-std`, and `--dot` help texts are corrected or added;
  `--mangling` and `--no-edges` are in the README option table.
- Removed stale committed outputs from `fixtures/cpp_complex`,
  `fixtures/cpp_complex2`, and `fixtures/rust_indirect`.
- The ziggy fixture test skips instead of failing when cargo-afl's AFL++
  plugins are not installed for the current rustc.
- Soundness: a callback stored into memory that a function without a body
  returns (`ext_get_hooks()->on_error = my_error`), directly, through a defined
  wrapper, or through loaded pointers, counts as an escape. Allocator results
  (`malloc`, `operator new`, `noalias` returns, …) stay the program's own.
- Soundness: a callback that external code calls may receive memory the library
  owns. Stores it makes through its pointer parameters
  (`my_init(struct ops *o) { o->cb = my_cb; }`) now count as escapes. The set
  of such callbacks is grown to a fixed point.
- Soundness: setters that copy a callback out of another struct
  (`o->cb = t->cb`), pick one with `?:` or a PHI, or copy a struct with
  `memcpy` now propagate it to the caller's object. `memcpy`/`memmove` copies,
  atomic exchanges and compare-exchanges count as stores in the value flow, so
  callbacks published with `atomic_exchange` are no longer missed.
- Calls to LLVM intrinsics (`memcpy`, `lifetime`, …) no longer count as handing
  a callback to external code, so they do not create constructor roots or
  externally-called callbacks.
- New JSON field `escaped_from_unreached` (and `summary.escaped_from_unreached`):
  unreachable functions that only unreached code hands to external code. The
  analyzer prints a note listing the first few.
- `interesting`: the implicit `this` of a C++ method no longer makes the method
  `interesting` on its own; the pointer chain still passes through it, and
  `dead_end` treats such a method as part of the chain. `this` is recognized
  from full debug info, or without it (including `-gline-tables-only`) from
  constructors/destructors, qualified methods, Itanium vtable entries, and
  methods whose mangled signature has no parameters. Internal-linkage
  functions in optimized bitcode keep counting `this`, since dead-argument
  elimination may have removed it.
- Confidence: vtable-slot evidence is split by kind. A C++ slot matches only a
  call that loads the vtable pointer out of the object it passes as `this`, in
  C++ code or code of unknown language; a Rust slot matches only a call in Rust
  code. Previously a C call through a function-pointer pointer rated every
  class's first virtual function and every Rust `drop_in_place` `medium`.
- Calls to LLVM intrinsics no longer add escape edges unless a function is
  passed to them directly (`llvm.init.trampoline`); `memcpy`/`memmove` are
  modelled as copies, and a copy into externally owned memory is an escape. On
  libxml2 at `-O2` this drops 5,572 spurious indirect edges (for example 129
  from the integer helper `compute_minrun`) with the reachable set unchanged.
- `--dump-edges` lists each function's callees sorted, and setter
  propagation no longer depends on pointer order, so repeated runs print
  identical output.


### v1.1
- C/C++ "build is CACHED" warning is confirmed against the artifact's mtime, so a
  single up-to-date subdirectory in a recursive `make` no longer triggers a false
  cache warning when the analyzed artifact was actually rebuilt.
- Indirect resolution precision: escape value-flow follows exact operands
  (PHI/select/GEP/aggregate) and the conservative address-taken fallback fires
  only for genuinely unresolved cast flows, trimming spurious low-confidence
  targets without shrinking the sound reachable set.
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
