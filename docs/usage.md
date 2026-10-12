# Usage

`reachability` has two commands: `check-toolchain` and `run`. Rust-specific
build details are in [rust.md](rust.md), and every error and warning is
explained in [troubleshooting.md](troubleshooting.md).

The outputs on this page come from reachability 1.2-dev (commit `f5adeab`)
with LLVM 23.

## `reachability check-toolchain`

Finds the analyzer, `clang`, `clang++`, `llvm-link` and, if installed, `rustc`,
and checks that their LLVM versions fit together. The rules are in
[llvm-support.md](llvm-support.md). Run it once before the first `run`.

```
$ reachability check-toolchain
OK: analyzer toolchain on LLVM 23 (min 21); rustc LLVM 22
  clang     /usr/bin/clang-23
  clang++   /usr/bin/clang++-23
  llvm-link /usr/bin/llvm-link-23
  analyzer  /prg/fuzz-reachability/analyzer/build/reachability-analyzer
  gllvm CC  /usr/bin/clang-23
  gllvm CXX /usr/bin/clang++-23
  gllvm link /usr/bin/llvm-link-23
```

It exits non-zero when the versions do not fit. Without `rustc` it prints
`no rustc on PATH (Rust targets unavailable; C/C++ ready)` and exits zero.

Tests: `test_cli.py::test_check_toolchain_ok`,
`::test_check_toolchain_without_rustc_is_ok`, and `test_toolchain.py`.

## `reachability run`

Builds the project, merges its bitcode and writes the reachable set.

```bash
reachability run --lang <target> --project <dir> [options]
```

| Option | Default | Meaning |
|---|---|---|
| `--project DIR` | required | Project to build and analyze |
| `--lang TARGET` | required | Target type; sets the build method and the default entries, see [Target types](#target-types) |
| `--entry NAME` | per `--lang` | Function to start from; repeatable, see [Entry resolution](#entry-resolution) |
| `--out FILE` | `reachability.json` in `--project` | JSON report, see [Output files](#output-files) |
| `--reached FILE` | `reached.txt` next to `--out` | Allowlist of reachable functions |
| `--not-reached FILE` | `not_reached.txt` next to `--out` | Ignorelist of unreachable functions |
| `--dot FILE` | none | Also write the reachable call graph as Graphviz DOT |
| `--no-edges` | off | Leave the `edges` array out of the report |
| `--build-cmd CMD` | detected | Shell command that builds the project, see [C and C++ builds](#c-and-c-builds) and [rust.md](rust.md#native-harness-builds) |
| `--capture {gllvm,rllvm}` | `gllvm` | Wrappers that record the bitcode, see [Capturing with rllvm](#capturing-with-rllvm) |
| `--artifact PATH` | detected | C/C++, or any target with `--capture rllvm`: the built file to take bitcode from, see [Artifact selection](#artifact-selection) |
| `--static-libs {auto,none,all}` | `auto` | C/C++, or any target with `--capture rllvm`: which static archives to analyze in full, see [Static libraries](#static-libraries) |
| `--optimize` | off | Analyze at the build's own optimization level, see [Source-faithful build](#source-faithful-build) |
| `--profile {debug,release}` | tool default | Rust build profile, see [rust.md](rust.md#profile-and-codegen-units) |
| `--codegen-units N` | detected | `rust` and `mixed`: rustc `-Ccodegen-units`, see [rust.md](rust.md#profile-and-codegen-units) |
| `--mangling {auto,legacy,v0}` | `auto` | Rust symbol mangling of the analysis build, see [rust.md](rust.md#mangling) |
| `--build-std` | off | `rust` and `mixed`: build the standard library from source, see [rust.md](rust.md#standard-library) |
| `--include-process-lifecycle-roots` | off | Also start from constructors, destructors and ifunc resolvers, see [Process-lifecycle roots](#process-lifecycle-roots) |
| `--no-name-roots` | off | Do not start from functions found by runtime name lookup; can under-report, see [analysis.md](analysis.md#runtime-name-lookup) |
| `--clean` | off | Remove earlier outputs and build state first, see [Clean builds](#clean-builds) |
| `--backend NAME` | none | Ignored; prints a warning |
| `-v`, `--verbose` | off | Print each stage, the commands it runs, the build output and the collected bitcode files |

`reachability --version` prints the driver version.

### Target types

| `--lang` | Bitcode from | Default entries |
|---|---|---|
| `c` | gllvm (`gclang`) | `main`, `LLVMFuzzerTestOneInput` |
| `cpp` | gllvm (`gclang++`) | `main`, `LLVMFuzzerTestOneInput` |
| `rust` | `cargo build` with `--emit=llvm-bc` | `main` |
| `mixed` | gllvm and `cargo build`, merged | `LLVMFuzzerTestOneInput` |
| `libfuzzer` | `cargo fuzz build` | `fuzz_target!` |
| `ziggy` | `cargo ziggy build --no-honggfuzz` | `main` |
| `afl` | `cargo afl build` | `main` |

`c` and `cpp` start from both `main` and `LLVMFuzzerTestOneInput`, so one
setting covers a normal program and a fuzz harness. A default entry that
matches nothing is a harmless warning, because the roots are combined. `rust`
only starts from `main`; a `staticlib` harness needs
`--entry LLVMFuzzerTestOneInput`.

`libfuzzer`, `ziggy` and `afl` build through the fuzzer's own command. `rust`
and `mixed` run a plain `cargo build`. Both are described in
[rust.md](rust.md). With `--capture rllvm`, every target takes its bitcode
from one build instead; see [Capturing with rllvm](#capturing-with-rllvm).

Test: `test_cli.py::test_target_entry_defaults`.

### Entry resolution

You never need to type a mangled symbol. An `--entry` token matches every
function where one of these fits:

- the exact mangled symbol (`_Z3foov`);
- the exact demangled name (`foo()`);
- a `::name` suffix of the demangled name, so `main` also finds `crate::main`;
- the alias `fuzz_target!`, which means `LLVMFuzzerTestOneInput` and
  `rust_fuzzer_test_input`.

Matching several functions only adds roots. For a Rust binary, `main` finds
both the C `main` that starts the Rust runtime and the real Rust `main`.

A token that matches nothing is reported as a warning, also without
`--verbose`; the other entries still run. When no entry matches, the analyzer
stops and suggests similar names. Analyzer warnings always go to stderr, never
into the JSON.

Tests: `test_analyzer_core.py::test_entry_main_resolves_rust_and_c`,
`::test_entry_demangled_name`, `::test_entry_fuzz_target_alias`,
`::test_rust_entry_rooting`, `::test_missing_entry_suggests_near_miss`,
`::test_no_entry_resolved_errors`, and
`test_analyze.py::test_analyzer_warnings_are_always_forwarded`.

### Process-lifecycle roots

Besides the entries and the [runtime name lookup](analysis.md#runtime-name-lookup)
roots, the analysis always starts from two kinds of code that a fuzzer runs
before the first input:

- **`LLVMFuzzerInitialize`**, whenever `LLVMFuzzerTestOneInput` is an entry.
  libFuzzer and AFL++'s libFuzzer driver call it once at startup, and harnesses
  use it to register callbacks such as error handlers or allocators.
- **Callbacks that constructors hand to external code.** A constructor in
  `llvm.global_ctors` (C++ static registration, `__attribute__((constructor))`)
  that passes a function to code outside the bitcode makes that function
  callable later. The callback becomes a root; the constructor itself does not.
  Exit-time registrations (`atexit`, `__cxa_atexit` for static destructors,
  `at_quick_exit`, `on_exit`, `__cxa_thread_atexit`) are shutdown code and are
  not rooted this way.

Both appear in the report's `entries` and are noted on stderr.

`--include-process-lifecycle-roots` is for when process start and shutdown
belong to what you analyze. It also roots the constructors, destructors and
ifunc resolvers themselves (and with them everything constructors register,
exit handlers included), and `LLVMFuzzerInitialize` even without
`LLVMFuzzerTestOneInput`. Malformed lifecycle records produce a warning.

Tests: `test_analyzer_core.py::test_callbacks_handed_out_before_the_first_input`,
`::test_initialize_is_rooted_only_for_libfuzzer_harnesses`,
`::test_constructor_bodies_are_rooted_only_on_request` and
`::test_process_lifecycle_roots_are_opt_in`.

## C and C++ builds

The driver runs the build with `CC=gclang` and `CXX=gclang++`. gllvm compiles
every file twice: once to the normal object, and once to a bitcode file whose
path it records in the object. The driver points gllvm at exactly the `clang`,
`clang++` and `llvm-link` that `check-toolchain` reports.

Without `--build-cmd` the driver picks the first build file it finds:

| File | Command |
|---|---|
| `configure` | `./configure && make`, adding `--disable-shared`, `--enable-static` and `--disable-lto` when `./configure --help` lists them |
| `Makefile` | `make` |
| `CMakeLists.txt` | `cmake -S . -B build-reachability -DBUILD_SHARED_LIBS=OFF -DCMAKE_INTERPROCEDURAL_OPTIMIZATION=OFF && cmake --build build-reachability` |
| `build.ninja` | `ninja` |
| `meson.build` | `meson setup build-reachability --default-library=static -Db_lto=false && ninja -C build-reachability` |
| `autogen.sh` | `./autogen.sh && ./configure --disable-shared --enable-static --disable-lto && make` |
| `configure.ac` or `configure.in` | `autoreconf -i && ./configure --disable-shared --enable-static --disable-lto && make` |
| none | `make` |

The extra flags have two reasons:

- **Static libraries.** A shared library is linked separately, so its bitcode
  never reaches the target.
- **No LTO.** gllvm cannot record bitcode under `-flto`.

CMake and Meson configure into `build-reachability/`, not into your own
`build/`. Both tools ignore a changed `CC` or `CXX` once a build directory
exists, so reusing yours would skip gllvm and also write these options into
your cache. A plain `make` or `ninja` tree has no portable switch for either
setting and runs unchanged.

`--build-cmd` replaces all of this. The command runs in a shell, with gllvm
still set as the compiler, so it must not enable LTO itself.

Tests: the `test_acquire_c.py::test_detect_build_cmd_*` tests,
`::test_configure_flags_adds_disable_lto_when_advertised`,
`::test_gllvm_env_pins_exact_tools` and `::test_build_env_sets_wrappers`.

### Artifact selection

The driver takes the bitcode from one built file, the artifact.

- **With `--artifact PATH`** (relative to `--project`), that file must exist,
  be an executable, shared library, archive or object, and carry bitcode.
  Otherwise the run fails; it never falls back to searching.
- **Without it**, the driver searches the project. It prefers files that carry
  gllvm's bitcode section, then files this run built, then executables over
  shared libraries over archives over objects, then the newest. When several
  files tie for first place, the run stops and lists them.

The search skips the leftovers of build-system checks. Those checks compile
with `gclang` as well, so their outputs carry bitcode and would otherwise tie
with the real target:

- CMake's `CMakeFiles/` directories (compiler-ID and ABI-check executables)
- Meson's `meson-private/` directory (sanity-check executable)
- autoconf's `conftest*` files, including the hidden `.conftest.c.o` that gllvm
  writes next to each probe and `configure` does not remove

An object inside `CMakeFiles/` can still be analyzed with `--artifact`.

Tests: `test_acquire_c.py::test_missing_explicit_artifact_never_discovers`,
`::test_unsupported_explicit_artifact_never_discovers`,
`::test_omitted_artifact_uses_discovery`,
`::test_auto_discovery_rejects_equally_plausible_artifacts`,
`::test_find_artifacts_prefers_executable_over_object`,
`::test_find_artifacts_prefers_bitcode_marker`,
`::test_find_artifacts_prefers_this_runs_output_over_kind` and
`::test_find_artifacts_skips_build_system_probes`.

### Static libraries

A binary linked against a static library only contains the archive members
the linker pulled in. Functions in the other members would be missing from the
report instead of showing as unreachable. `--static-libs` controls this:

| Value | Effect |
|---|---|
| `auto` | Also analyze, in full, every archive with bitcode that the artifact links |
| `none` | Only the linker's view |
| `all` | Every archive with bitcode in the project tree |

Adding the rest of a library can only add unreachable functions; the reachable
set stays the same. The driver uses the exact object lists gllvm records, so
no linked object is dropped. If an archive cannot be listed or fully
extracted, the run fails and publishes nothing. Thin archives, which Meson
builds for static libraries it does not install, are handled like regular ones:
their bitcode is read from the member files they point to.

`auto` reads the list of linked archives from the artifact, so it only works
when the artifact is a linked executable or shared library. For an object file
use `all`.

`fixtures/c_static_archives` links `libfirst.a` and `libsecond.a`, each with
one used and one unused function:

```
$ reachability run --lang c --project fixtures/c_static_archives --artifact app
warning: unresolved entry symbols: LLVMFuzzerTestOneInput
build command: make
artifact: …/c_static_archives/app
static library (full): libsecond.a
static library (full): libfirst.a
reachable 3 / defined 5  (0 indirect-only, 0 low-confidence, 2 unreachable)  [backend=type-based]
```

With `--static-libs none` it reports `reachable 3 / defined 3`: the unused
functions `first_dead` and `second_dead` are missing instead of unreachable.

Tests: `test_cli.py::test_run_static_archives_end_to_end`,
`::test_static_archive_failure_publishes_no_report`, and the
`test_acquire_c.py::test_plan_static_libs_*`, `::test_include_static_libs_*`
and `::test_thin_archive_*` tests.

### Source-faithful build

The report must describe the same functions as the binary you instrument.
Optimization changes which functions exist: inlining, dead-code elimination
and devirtualization delete functions the source defines.

So by default the driver compiles the bitcode at `-O0 -fno-inline
-fno-inline-functions`. It sets these in `LLVM_BITCODE_GENERATION_FLAGS`, which
gllvm applies to the bitcode compile only; the native object keeps the
project's own `-Ox`. The report then lists the functions the source defines,
which is what `llvm-cov` reports too. `-fno-inline` alone is not enough: at
`-O3` the optimizer still deletes functions through dead-code elimination and
devirtualization of constant function-pointer tables. One case remains even at
`-O0`: clang always inlines `static` functions marked `always_inline` and then
drops them, so they are not in the report. `-O0` bitcode is larger, so merging
and analysis need more time and memory.

If `LLVM_BITCODE_GENERATION_FLAGS` already sets an `-O` level, that level is
kept and only the no-inline flags are added. This is the way out for a file
that only compiles with optimization.

`--optimize` turns this off and analyzes the build's own optimization level, so
the report and the [function metrics](analysis.md#function-metrics) match one
specific binary. AFL++'s `afl-cc` and `afl-clang-fast` default to `-O3`, while
libFuzzer and honggfuzz builds use whatever `-O` you pass. To match a default
AFL++ build, run `CFLAGS=-O3 CXXFLAGS=-O3 reachability run --optimize …`.

This only works where the driver runs the compiler. Bitcode from a prebuilt
`--artifact`, from a build command that recompiles nothing, or from a project
already built with `gclang` keeps its own optimization. The run detects this
and prints the
[post-optimizer warning](troubleshooting.md#the-analyzed-bitcode-was-built-with-optimization-enabled).

Rust builds are forced to opt-level 0 in the same way; see
[native harness builds](rust.md#native-harness-builds) and
[plain cargo builds](rust.md#plain-cargo-builds).

Tests: `test_acquire_c.py::test_build_env_injects_opt0_by_default`,
`::test_build_env_injects_no_inline_by_default`,
`::test_build_env_keeps_inherited_opt_level`,
`::test_build_env_optimize_omits_injection`,
`::test_acquire_forwards_optimize_to_build_env`, and the
`test_report.py::test_optimization_advisory_*` tests.

## Capturing with rllvm

`--capture rllvm`, or `REACHABILITY_CAPTURE=rllvm`, records the bitcode with
[rllvm](https://github.com/h1994st/rllvm) 0.7.1 or newer instead of gllvm
(`brew install h1994st/tap/rllvm` or `cargo install rllvm`). `rllvm-cc`,
`rllvm-cxx`, `rllvm-rustc`, `rllvm-get-bc` and `rllvm-info` must be on `PATH`.
gllvm stays the default.

**C and C++.** The build runs with `CC=rllvm-cc` and `CXX=rllvm-cxx`. The
driver writes a private `RLLVM_CONFIG` that names the same `clang`, `clang++`
and `llvm-link` as for gllvm, and passes the
[source-faithful flags](#source-faithful-build), together with any
`LLVM_BITCODE_GENERATION_FLAGS`, as rllvm's `bitcode_generation_flags`, which
also apply to the bitcode compile only. Build command detection, artifact
selection and static libraries work as with gllvm. Archive members are matched
by the exact bitcode paths rllvm records rather than by object name: rllvm
names a bitcode file after its source and compilation
(`.parser_<hash>.o.bc`), not after the member object (`parser.o`).

**Rust and mixed.** rllvm also wraps rustc, so every target takes its bitcode
from one build and one extraction from the linked artifact, with no separate
Rust collection and no merge of the two:

| `--lang` | Build command | Wrapped |
|---|---|---|
| `rust` | `cargo build` | rustc |
| `libfuzzer`, `ziggy`, `afl` | the fuzzer's own command | rustc |
| `mixed` | the C/C++ build command | rustc, `CC`, `CXX` |

`--build-cmd` replaces the command in each case. A `mixed` build must link
the C/C++ code and the Rust crates into the artifact itself, for example
`--build-cmd 'cargo build && make && $CXX glue.o target/debug/libfoo.a -o fuzzer' --artifact fuzzer`.
Harness builds leave `CC` and `CXX` alone, so libFuzzer's C++ runtime stays
out of the report, and still run the clean command afterwards.

The rustc flags are the ones the per-crate collection adds: `-Copt-level=0`
unless `--optimize` (for `rust` and `mixed` with the profile's debug
assertions and overflow checks pinned), the `--mangling` flags, and
`--codegen-units` when given. Cargo applies the profile itself.

The bitcode is what the linker put into the artifact. Crates that only run at
build time, the dependencies of build scripts and proc macros such as `syn`,
`quote` or `cc`, are therefore not analyzed, whereas the per-crate collection
merges them in. A library-only crate yields only its own bitcode; point
`--artifact` at a binary that links it, such as the fuzz harness. The artifact
search also covers a cargo workspace's target directory and skips cargo's own
`deps/`, `build/` and `incremental/` copies in favor of the uplifted output.

Tests: the `test_acquire_c.py::test_rllvm_*` tests,
`::test_extract_bc_rllvm_writes_manifest_from_catalog`,
`::test_include_static_libs_rllvm_matches_members_by_bitcode_path`,
`::test_find_artifacts_prefers_cargo_uplifted_output`,
`::test_find_artifacts_searches_a_workspace_target_outside_the_project`,
`test_cli.py::test_run_static_archives_end_to_end` and
`test_reachable_sets.py::test_mixed_c_rust_reachable_from_one_rllvm_extraction`.

## Output files

The file formats are described in [output.md](output.md).

`--out`, `--reached`, `--not-reached` and `--dot` always name files. The run
checks them before it removes or builds anything:

- an existing directory is rejected;
- the parent directory must exist;
- no two of them may be the same file, also after resolving symlinks.

All files are first written next to their destinations and then replaced
together, after the build, merge, analysis and checks have succeeded. A failed
run keeps the previous set of files. Each run keeps its bitcode and
`merged.bc` in its own temporary directory, so two runs in one project do not
share them.

At the end the run prints a summary line:

```
reachable 3 / defined 4  (0 indirect-only, 0 low-confidence, 1 unreachable)  [backend=type-based]
```

It may add a note when many reachable functions have no body in the bitcode,
and a warning when the bitcode was optimized. Both are explained in
[troubleshooting.md](troubleshooting.md).

Tests: `test_cli.py::test_out_optional_defaults_to_project`,
`::test_out_directory_is_rejected`, `::test_output_collision_lexical_alias`,
`::test_output_collision_symlink_alias`,
`::test_output_transaction_rolls_back_mid_publish`,
`::test_output_staging_io_failure_is_domain_error` and
`::test_concurrent_runs_use_isolated_intermediates`.

## Clean builds

A build only produces bitcode for what it compiles, so a build that reuses
earlier results gives stale or no bitcode. `--clean` first checks every output
path, then:

- removes the selected output files and a `merged.bc` in the project;
- for Rust targets and `mixed`: runs `cargo clean`, also in `fuzz/` when it
  exists;
- for C, C++ and `mixed`: removes `build-reachability/`, runs the clean command
  of each configured build tree (make, ninja, CMake, Meson), and removes the
  files that earlier runs recorded in
  `.reachability-cache/owned-c-artifacts.json`.

Other `.o`, `.bc` and manifest files in the project are left alone.

Tests: `test_cli.py::test_clean_c_removes_build_artifacts_and_outputs`,
`::test_clean_c_runs_build_system_clean`, `::test_clean_rust_removes_target_dir`,
`::test_clean_unlinks_only_selected_files_and_symlinks` and
`::test_clean_rejects_directory_destinations_without_mutation`.

## Environment variables

| Variable | Meaning |
|---|---|
| `REACHABILITY_ANALYZER` | Analyzer binary; default `analyzer/build/reachability-analyzer` in the repository |
| `CLANG`, `CLANGXX`, `LLVM_LINK` | Override one tool; otherwise the version matching the analyzer's LLVM is used (`clang-23`, …), then the plain name |
| `PATH` | Must contain gllvm's `gclang`, `gclang++` and `get-bc` for `c`, `cpp` and `mixed`, or rllvm's tools with `--capture rllvm` |
| `REACHABILITY_CAPTURE` | Default for `--capture`, see [Capturing with rllvm](#capturing-with-rllvm) |
| `LLVM_BITCODE_GENERATION_FLAGS` | Extra flags for gllvm's bitcode compile, see [Source-faithful build](#source-faithful-build) |
| `RUSTFLAGS`, `CARGO_ENCODED_RUSTFLAGS` | Kept and merged with the driver's flags, see [rust.md](rust.md#plain-cargo-builds) |
| `CARGO_TARGET_DIR` | Honoured when collecting Rust bitcode |
| `AFLRS_REQUIRE_PLUGINS` | Set to `1` for native harness builds unless you set it, see [rust.md](rust.md#native-harness-builds) |

## The analyzer

`reachability run` calls `analyzer/build/reachability-analyzer`. You can also
run it on any `.ll` or `.bc` file:

```bash
reachability-analyzer merged.bc --entry main --out report.json \
  --reached-out reached.txt --not-reached-out not_reached.txt
```

| Option | Default | Meaning |
|---|---|---|
| `<input>` | required | `.ll` or `.bc` module |
| `--entry NAME` | `LLVMFuzzerTestOneInput` | Function to start from; repeatable, matched as in [Entry resolution](#entry-resolution) |
| `--out FILE` | stdout | JSON report |
| `--reached-out FILE` | none | Allowlist |
| `--not-reached-out FILE` | none | Ignorelist |
| `--dot FILE` | none | Graphviz DOT graph |
| `--no-edges` | off | Leave out the `edges` array |
| `--run-info JSON` | none | JSON object copied into the report's `run` field |
| `--include-process-lifecycle-roots` | off | As on `reachability run` |
| `--no-name-roots` | off | As on `reachability run` |
| `--indirect-any` | off | Debugging: every indirect call can reach every address-taken function |
| `--dump-edges` | off | Debugging: print every call edge, sorted, and exit |
| `--selftest-demangle SYMBOL` | none | Print the demangled symbol and exit |
| `--backend NAME` | none | Ignored; prints a warning |
| `--version` | | Prints `reachability-analyzer (LLVM N)` |

`--help` also lists LLVM's own options. The analyzer exits with status 1 when
the input cannot be read, no entry resolves, `--run-info` is not a JSON
object, or an output cannot be written.

Tests: `test_analyzer_core.py::test_run_info_and_edges_toggle`,
`::test_dump_edges_is_sorted_and_stable`, `::test_v0_demangle_selftest`,
`::test_backend_flag_deprecated_and_ignored` and
`::test_indirect_any_includes_other`.
