# Troubleshooting

Errors end the run with `error: …` on stderr and exit status 1, without a
Python traceback. Warnings and notes do not stop the run. Run with `-v` to see
each stage, the commands it runs and the live build output.

Test: `test_cli.py::test_expected_cli_error_has_no_traceback`.

## Toolchain

### Analyzer binary not found

```
error: analyzer binary not found: …/analyzer/build/reachability-analyzer
build it with `make build`, or set $REACHABILITY_ANALYZER
```

Build the analyzer (see [development.md](development.md#building-the-analyzer))
or point `REACHABILITY_ANALYZER` at it.

### A tool is missing or has the wrong LLVM version

```
error: required tool not found: clang (set $CLANG)
error: toolchain LLVM major mismatch (analyzer = M): …
```

`clang`, `clang++` and `llvm-link` must have the same LLVM major version as
the analyzer. The driver looks for `clang-23` and so on first, then the plain
names. Install the matching tools, or set `CLANG`, `CLANGXX` and `LLVM_LINK`.
An analyzer built against LLVM 20 or older is rejected; rebuild it against
LLVM 21 or newer.

Tests: `test_toolchain.py::test_check_coherence_raises_on_tool_mismatch` and
`::test_check_coherence_rejects_below_minimum`.

### rustc's LLVM is newer than the analyzer's

```
error: rustc's bundled LLVM (23.1.1) is newer than the analyzer toolchain's LLVM (23.0.0); newer bitcode cannot be read by older tools. …
```

LLVM can read older bitcode but not newer. The full version counts, not only
the major: a distro LLVM 21.0.0 cannot read the bitcode of a rustc with LLVM
21.1.1 (`llvm-link: Invalid record`). Either build the analyzer against a newer
LLVM (`make build LLVM_MAJOR=…`), or use a rustc with an older LLVM. A
project's `rust-toolchain` file can select a different rustc than your default;
`RUSTUP_TOOLCHAIN=nightly` overrides it. See
[llvm-support.md](llvm-support.md).

Test: `test_toolchain.py::test_check_coherence_rejects_rustc_newer_than_toolchain`.

## C and C++ builds

### gclang not found

```
error: gclang not found on PATH; run scripts/setup.sh
```

Put gllvm on `PATH`: `export PATH="$(go env GOPATH)/bin:$PATH"`.

### The build failed

```
error: build failed (exit N):
…
```

The build command failed. The error shows the build output; `-v` streams it
live. Older projects may need extra configure flags on a new system, for
example `--without-lzma`; pass the whole command with `--build-cmd`.

### Several artifacts are equally plausible

```
error: multiple equally plausible build artifacts remain; pass --artifact with one of:
  build-reachability/fuzz_target
  build-reachability/CMakeFiles/4.2.3/CMakeDetermineCompilerABI_CXX.bin
  build-reachability/CMakeFiles/4.2.3/CompilerIdCXX/a.out
```

Pass `--artifact` with the file you want. CMake's compiler checks always cause
this, see [usage.md](usage.md#artifact-selection).

Test: `test_acquire_c.py::test_auto_discovery_rejects_equally_plausible_artifacts`.

### The explicit artifact is missing or unsupported

```
error: explicit artifact does not exist: …
error: explicit artifact is unsupported: …
```

`--artifact` is relative to `--project` and must name an executable, shared
library, archive or object. The driver never falls back to searching.

Tests: `test_acquire_c.py::test_missing_explicit_artifact_never_discovers` and
`::test_unsupported_explicit_artifact_never_discovers`.

### No bitcode could be extracted

```
error: no build artifact with embedded bitcode found under …; pass --artifact PATH to the built binary/object/archive
error: get-bc could not extract bitcode from any detected artifact:
error: get-bc could not extract bitcode from explicit artifact …:
```

When the build log shows why, the error adds a `Likely cause:` line:

| Likely cause | Why | Fix |
|---|---|---|
| link-time optimization strips gllvm bitcode | gllvm records no bitcode under `-flto` and ignores `-fno-lto` | Remove `-flto`: CMake `-DCMAKE_INTERPROCEDURAL_OPTIMIZATION=OFF`, Meson `-Db_lto=false`, configure `--disable-lto`, or null the Makefile's LTO variable (AFL++: `make CFLAGS_FLTO=`) |
| gllvm's bitcode compile failed for some translation units | A file fails to compile at `-O0`; gclang still exits 0 | Set `LLVM_BITCODE_GENERATION_FLAGS` to an `-O` level, or pass `--optimize` |
| the artifact has an empty/name-only .llvm_bc section | The file was built by `afl-clang-fast` or `clang -flto`, not gllvm | Rebuild it with `gclang`/`gclang++` |
| a compiler cache bypassed the gllvm wrapper | ccache or sccache replayed cached objects | Clear (`ccache -C`) or disable the cache, then rebuild |
| some translation units produced no bitcode | Assembly-only files, or files with no input | Expected; only a problem if the entry lives there |

Tests: `test_diagnostics.py` and `test_acquire_c.py::test_acquire_reports_lto_when_no_artifact`.

### The build was cached

```
warning: the build is CACHED (nothing was recompiled); the extracted bitcode reflects the existing artifact, not this run. Rebuild from clean if the target or its flags changed.
```

The build tool did nothing and the artifact is older than the run, so the
bitcode is from an earlier build, perhaps with other flags. Rerun with
`--clean`.

Tests: the `test_acquire_c.py::test_build_is_cached_*` tests.

### Static-library expansion failed

```
error: static-library expansion is incomplete; refusing to publish a complete-looking report:
error: empty target manifest for …; cannot complete static-library expansion
error: cannot list static archive members: …
```

An archive the run should analyze in full could not be read or extracted, so
the run stops instead of writing an incomplete report. Fix the archive, or use
`--static-libs none` to analyze only what the linker pulled in. The line
`static library (genuinely empty): …` is only information.

Tests: `test_acquire_c.py::test_include_static_libs_partial_failure_is_atomic`,
`::test_include_static_libs_distinguishes_empty_target_manifest`,
`::test_include_static_libs_reports_genuinely_empty_archive` and
`test_cli.py::test_static_archive_failure_publishes_no_report`.

## Rust builds

### A crate failed to compile

```
error: cargo failed to compile a crate, so the bitcode set would be incomplete -- fix the build first:
error: cargo build failed (exit N):
error: the fuzzer build failed; fix it first:
```

Fix the build. A failing final link is expected under `--emit=llvm-bc` and
does not cause this error.

Tests: `test_acquire_rust.py::test_compile_errors_flags_real_failure_not_link`
and `::test_failed_cargo_never_uses_stale_bitcode`.

### AFL++ plugins are not installed

```
error: the fuzzer build failed; fix it first:
  …AFL++ plugins are not installed; run `cargo afl config --build --force --plugins`
  AFL++ LLVM plugins are required (AFLRS_REQUIRE_PLUGINS=1); build them with `cargo afl config --build --plugins --force`.
```

cargo-afl needs its AFL++ plugins for the current rustc. Run
`cargo afl config --build --plugins --force`, with `LLVM_CONFIG` set to an
`llvm-config` that matches rustc's LLVM, for example `llvm-config-22`. A nightly
update needs a new plugin build. See [rust.md](rust.md#native-harness-builds).

### The Rust build was cached

```
error: the build was CACHED (the tool recompiled nothing), so rustc never ran. Clean it first (e.g. `cargo clean`, or for cargo-fuzz remove fuzz/target) and re-run so every crate compiles under the wrapper.
warning: cargo recompiled nothing -- this build is CACHED, so the bitcode is from an earlier compile and may be stale. Run `cargo clean` and re-run for a fresh build.
```

rustc only writes bitcode for crates it compiles. The error comes from native
harness builds, the warning from plain cargo builds. Rerun with `--clean`.

Tests: `test_acquire_rust.py::test_build_looks_cached_cargo`.

### No fresh bitcode

```
error: no invocation-fresh .bc produced under …
error: no bitcode was captured. Clean it first …
```

The build ran but wrote no bitcode in this run. Rerun with `--clean`. A custom
`--build-cmd` must run rustc through cargo, so the wrapper can see it.

### Several builds of one crate

```
warning: found bitcode from N builds of crate 'x'; picking the newest by mtime, which can be stale if this crate was cached. Re-run with --clean for a fresh build.
```

The target directory holds bitcode from more than one build of a binary or
library crate. Rerun with `--clean`.

Test: `test_acquire_rust.py::test_named_bc_paths_warns_on_multiple_build_hashes`.

### Fallback bitcode collection

```
warning: fallback bitcode collection: cargo emitted no artifact stream; selected N of M invocation-fresh .bc files
```

cargo reported no artifacts, so the driver took the bitcode files this run
wrote. Check the list with `-v`.

Test: `test_acquire_rust.py::test_fallback_preserves_two_live_versions_and_all_codegen_units`.

## Merging

```
error: llvm-link failed (exit 1):
error: Linking globals named 'same': symbol multiply defined!
```

Two inputs define the same function. With `--static-libs all` this happens
when two archives contain the same code; use `--static-libs auto` or narrow
the archives. An `Invalid record` error means bitcode from a newer LLVM, see
[above](#rustcs-llvm-is-newer-than-the-analyzers).

Test: `test_link.py::test_duplicate_definition_failure_is_reported`.

## Analysis

### No entry resolved

```
error: analyzer failed (exit 1):
warning: unresolved entry symbols: main
error: no entry symbol resolved. Requested: main
```

None of the entries exists. The analyzer lists similar defined names when it
finds any. Pass `--entry` with the right function; a Rust `staticlib` harness
needs `--entry LLVMFuzzerTestOneInput`. See
[usage.md](usage.md#entry-resolution).

Tests: `test_analyzer_core.py::test_no_entry_resolved_errors` and
`::test_missing_entry_suggests_near_miss`.

### Unresolved entry symbols

```
warning: unresolved entry symbols: main
```

One entry matched nothing, but another did. This is normal for `c` and `cpp`,
which try both `main` and `LLVMFuzzerTestOneInput`.

### Notes about added roots

```
note: added LLVMFuzzerInitialize as a harness root (libFuzzer calls it once before the first input)
note: added N root(s) referenced by name (dlsym/dlopen-by-name reachability): …
note: added N root(s) for callbacks that constructors hand to external code: …
note: added N process lifecycle root(s): …
```

Information only. The roots are explained in
[usage.md](usage.md#process-lifecycle-roots) and
[analysis.md](analysis.md#runtime-name-lookup).

### Functions handed out only by unreached code

```
note: N function(s) stay unreachable because only unreached code hands them to external code: …
```

These callbacks would be reachable if the code that registers them ran. This
often means the harness skips an initialization step that the real program
runs. They are listed in the report's `escaped_from_unreached`.

### Malformed lifecycle records

```
warning: malformed llvm.global_ctors lifecycle records
warning: malformed ifunc resolver for …
```

The module has a constructor, destructor or ifunc entry the analyzer cannot
read. It is skipped.

## After the run

### Many reachable functions without bitcode

```
note: N external callees are reachable but have no bitcode body (system libc, precompiled libraries, Rust std without --build-std, or asm units). …
```

Printed when the number of reachable functions without a body is more than
half the number of reachable defined functions. The allowlist cannot
instrument them; use the ignorelist. See [output.md](output.md#allowlist-or-ignorelist).

Tests: `test_report.py::test_external_advisory_triggers_when_many_external` and
`::test_external_advisory_silent_when_few`.

### The analyzed bitcode was built with optimization enabled

```
warning: the analyzed bitcode was built with optimization enabled (N of M compile units carry debug info marked isOptimized). …
```

The bitcode did not come from the driver's own source-faithful compile. The
usual reasons are a prebuilt `--artifact`, a build that recompiled nothing, or
a project already built with `gclang` before the run. Optimization has deleted
functions the source defines, so `llvm-cov` and cov-analysis rows for them
match neither list, and the coverage report looks complete but is not.

The evidence is `summary.optimized_compile_units` from debug info, or, for C
and C++ without debug info, how few definitions carry `noinline` or `optnone`.
Rerun with `--clean` so the driver compiles everything itself. Pass
`--optimize` if you want the optimized view. See
[usage.md](usage.md#source-faithful-build).

Tests: the `test_report.py::test_optimization_advisory_*` tests.

## Output files

```
error: --out must name a file, not a directory: …
error: parent directory for --out does not exist: …
error: output destinations collide after path resolution: --out and --reached
```

Fix the paths. These checks run before anything is removed or built. See
[usage.md](usage.md#output-files).

```
warning: could not remove …: …
warning: could not record analysis-owned C artifacts: …
```

`--clean` could not remove a file, or the run could not record which files it
built. In the second case a later `--clean` does not remove those files.

### `--backend` is ignored

```
warning: --backend is deprecated and ignored; the type-based backend is always used
```

Remove the option.

Test: `test_cli.py::test_backend_flag_deprecated_warns`.
