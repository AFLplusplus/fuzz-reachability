# Rust targets

Rust projects are built in one of two ways:

- **Native harness builds** (`--lang libfuzzer`, `ziggy`, `afl`) run the
  fuzzer's own build command.
- **Plain cargo builds** (`--lang rust`, and the Rust half of `mixed`) run
  `cargo build`.

Both add `--emit=llvm-bc` to every rustc call and collect one bitcode file per
crate and codegen unit. rustc's LLVM must not be newer than the analyzer's;
see [llvm-support.md](llvm-support.md). With `--capture rllvm`, both instead
take the bitcode from the linked artifact; see
[Capturing with rllvm](usage.md#capturing-with-rllvm).

The outputs on this page come from reachability 1.2-dev (commit `f5adeab`)
with LLVM 23 and rustc 1.99.0-nightly (2026-07-12, LLVM 22.1.8).

## Native harness builds

| `--lang` | Build command | Clean command afterwards |
|---|---|---|
| `libfuzzer` | `cargo fuzz build` | `cargo clean` in `fuzz/` |
| `ziggy` | `cargo ziggy build --no-honggfuzz` | `cargo ziggy clean` |
| `afl` | `cargo afl build` | `cargo afl clean` |

The driver sets `RUSTC_WRAPPER` to a small script. The script adds
`--emit=llvm-bc` to every rustc call and copies each crate's bitcode into a
private directory. The bitcode therefore has the same `cfg(fuzzing)`, features
and instrumentation as the binary you fuzz, and it does not matter where the
fuzzer writes its output. Build scripts and proc-macro crates are skipped.

- **`--profile release`** adds `--release` to the command.
- **`--build-cmd`** replaces the command, for example to pick features or one
  binary: `--build-cmd 'cargo afl build --features afl --bin afl_runner'`.
- **`--codegen-units` and `--build-std`** do not apply; the harness build
  decides.

**Source-faithful by default.** The wrapper also adds `-Copt-level=0`, so no
function is inlined away and the report matches what `llvm-cov` reports.
Because that build is not the one you want to fuzz, the driver runs the clean
command afterwards, and your next fuzzing build compiles from scratch.
`--optimize` keeps the harness's own optimization and skips the clean.

**AFL++ plugins.** cargo-afl and ziggy builds run with
`AFLRS_REQUIRE_PLUGINS=1`. Without the AFL++ LLVM plugins they then fail
instead of quietly building with weaker instrumentation that does not match
your fuzzer. The plugins are built per rustc version, so after a nightly update
run `cargo afl config --build --plugins --force` again, with an `llvm-config`
that matches rustc's LLVM (for example `LLVM_CONFIG=llvm-config-22`).

**Cached builds.** A build that compiles nothing runs rustc for no crate, so
there is no bitcode and the run fails with "the build was CACHED". Use
`--clean`.

`fixtures/rust_indirect` is a ziggy harness:

```
$ reachability run --lang ziggy --project fixtures/rust_indirect
note: added 1 root(s) referenced by name (dlsym/dlopen-by-name reachability): dlsym_resolved_target
rust bitcode (native build): 8 crate modules
reachable 1122 / defined 2022  (191 indirect-only, 47 low-confidence, 900 unreachable)  [backend=type-based]
```

Its `entries` are `main`, the Rust main `_RNvCsTPCUaflBzq_25rust_ziggy_indirect_calls4main`
and the name-lookup root `dlsym_resolved_target`.

Tests: `test_cli.py::test_native_build_cmd_defaults`,
`::test_native_build_cmd_release`, `::test_native_clean_uses_harness_command`,
`::test_native_clean_libfuzzer_targets_fuzz_dir`,
`::test_acquire_native_cleans_when_not_optimize`,
`::test_acquire_native_skips_clean_when_optimize`,
`::test_run_rust_indirect_fixture`, and in `test_acquire_rust.py`
`::test_native_sets_opt0_env_by_default`, `::test_native_optimize_clears_opt0_env`
and the `::test_bc_wrapper_*` tests.

## Plain cargo builds

For `--lang rust` and `mixed` the driver runs

```bash
cargo build --message-format=json-render-diagnostics [--release] [-Zbuild-std --target <host>]
```

with these rustc flags in `CARGO_ENCODED_RUSTFLAGS`:

1. `--emit=llvm-bc -Cembed-bitcode=yes -Ccodegen-units=N`;
2. your own flags: `CARGO_ENCODED_RUSTFLAGS` or `RUSTFLAGS` if set, otherwise
   `build.rustflags` from the cargo config files, merged from `$CARGO_HOME`
   down to the project the way cargo does it;
3. without `--optimize`: `-Copt-level=0`, and `-Cdebug-assertions` and
   `-Coverflow-checks` set to the profile's values;
4. the [mangling](#mangling) flags.

Step 2 keeps project flags such as `--cfg tokio_unstable`, which a plain
`RUSTFLAGS=…` would replace. Step 3 is needed because rustc turns debug
assertions on at opt-level 0, which would change which functions are compiled.

The bitcode list comes from cargo's messages about this build, not from a
search of the target directory, so files from earlier builds are never picked
up. Up to cargo 1.99 the files are in `target/<profile>/deps/`; from cargo
1.100 each unit has its own `target/<profile>/build/<pkg>/<hash>/out/`. Both
are searched, and `CARGO_TARGET_DIR` is honoured.

The final link may fail under `--emit=llvm-bc`; that is expected and ignored.
A compile error in any crate stops the run, because the bitcode would be
incomplete. A build that compiles nothing prints a warning that the bitcode may
be stale; use `--clean`.

Tests in `test_acquire_rust.py`: `::test_compose_rustflags_merges_project_config`,
`::test_compose_rustflags_keeps_caller_env`, `::test_config_rustflags_merges_hierarchy`,
`::test_compose_rustflags_appends_opt0_by_default`,
`::test_compose_rustflags_opt0_wins_over_inherited`,
`::test_compose_rustflags_pins_release_assertions_off`,
`::test_compose_rustflags_pins_debug_assertions_on`,
`::test_compile_errors_flags_real_failure_not_link`,
`::test_failed_cargo_never_uses_stale_bitcode`,
`::test_build_bc_paths_finds_per_unit_output_directory` and
`::test_target_dir_honors_environment`; also
`test_reachable_sets.py::test_rust_flag_switch_without_clean_uses_fresh_bitcode`.

### Profile and codegen units

Set these to match the fuzz binary's build. Both only apply to plain cargo
builds.

**`--profile {debug,release}`** picks the cargo profile; the default is
`debug`, like a bare `cargo build`. Without `--optimize` the opt level is
always 0, and the profile decides `debug_assertions`, `overflow_checks` and the
profile's other settings. With `--optimize` it also decides the opt level,
which turns rustc's generic sharing on (unoptimized) or off (optimized). That
decides which crate instantiates each generic, and so which instances exist and
what they are called.

**`--codegen-units N`** is passed to rustc as `-Ccodegen-units`. With
`--optimize` the units set the inlining boundaries, so they decide which
instances survive as functions. When you leave it out, the driver uses:

1. `codegen-units` from `[profile.<name>]` in `Cargo.toml`, searched from
   `--project` up to the workspace root (`<name>` is `dev` for `debug`);
2. otherwise cargo's default: 256 for `debug`, 16 for `release`.

So you usually only set `--profile`. Pass `--codegen-units` when the fuzz build
uses a value that is in neither place, for example one set with
`-Ccodegen-units` in `RUSTFLAGS`. `-v` prints the values used, such as
`profile=debug, codegen-units=256`.

Tests in `test_acquire_rust.py`: `::test_resolve_codegen_units_explicit_wins`,
`::test_resolve_codegen_units_from_manifest`,
`::test_resolve_codegen_units_cargo_defaults`,
`::test_manifest_codegen_units_walks_up_to_workspace_root` and
`::test_workspace_root_profiles_override_member_profiles`; also
`test_reachable_sets.py::test_workspace_profile_semantics_compile_time`.

### Standard library

Without `--build-std` the standard library comes precompiled, so its functions
appear as `external_declarations`. `--build-std` builds it from source with
`-Zbuild-std --target <host>`, so std functions become part of the graph. It
needs a nightly rustc and the `rust-src` component, and only applies to plain
cargo builds. The added `--target` changes v0 names, see
[below](#joining-names-across-builds).

Test: `test_acquire_rust.py::test_build_std_is_only_a_cargo_option`.

## Mangling

rustc has two symbol-mangling schemes. Legacy names look like
`_ZN8url_fuzz4main17h58c435803ec45a52E`, v0 names like
`_RNvCscjF3EmfBC9s_8url_fuzz4main`. Legacy and v0 names of the same function
share nothing, so the report only joins with a build that uses the same scheme.

`--mangling` sets the scheme of the analysis build, for every Rust `--lang`:

| Value | Effect |
|---|---|
| `auto` | Adds nothing; rustc's default applies. That is v0 on rustc 1.99, stable and nightly, and legacy on older versions such as 1.88. |
| `v0` | Adds `-Csymbol-mangling-version=v0` |
| `legacy` | Adds `-Zunstable-options -Csymbol-mangling-version=legacy`; needs a nightly rustc |

Pick the scheme of the build you join against:

- cargo-afl, ziggy and cargo-fuzz binaries use the toolchain default, so
  `auto` matches them when both use the same toolchain;
- a `-Cinstrument-coverage` build is always v0, so use `--mangling v0`.

The report's top-level `mangling` field says which scheme the bitcode has.
How the build-dependent end of a Rust name is handled is described in
[output.md](output.md#rust-names-and-key).

Tests: in `test_acquire_rust.py` the `::test_compose_rustflags_mangling_*` and
`::test_native_carries_mangling_*` tests; in `test_v0_mangling_probe.py`
`::test_v0_flag_produces_v0_symbols`, `::test_instrument_coverage_matches_explicit_v0`
and `::test_v0_stable_across_opt_levels`.

### Joining names across builds

`key` and the `*` in the lists only cover the build-dependent end of a Rust
name. Two more things must match:

- **The set of functions.** A wildcard cannot bring back a function that one
  build inlined away and the other kept. Match `--profile` and
  `--codegen-units` for plain cargo builds; native builds use the harness's own
  build and match it already.
- **The crate disambiguators (v0 only).** A v0 name contains every crate with
  a `Cs<hash>_` disambiguator, which rustc derives from cargo's `-C metadata`.
  The profile, the features and whether cargo gets `--target` change it; extra
  `RUSTFLAGS` do not. For example, `dep::dep_checksum` starts with
  `_RNvCs5zheXYwTW8r_3dep…` in a dev build, `_RNvCsiqyxleFSJ0j_3dep…` with
  `--release`, and `_RNvCs8ybOMRUuePv_3dep…` with
  `--target x86_64-unknown-linux-gnu`. Legacy names fold the same value into
  the hash that `key` drops, so legacy joins do not depend on it.

So for v0: native builds match automatically. Plain `--lang rust` builds
without `--target` (unless `--build-std` adds it), so pass the fuzz build's
`--profile`, and analyze a fuzz binary whose build passes `--target`
(cargo-fuzz always does) through its native route. When names still do not
match, a consumer with source locations can fall back to `file` and `line`, as
[cov-analysis](output.md#how-cov-analysis-joins) does.

## Entries

`--entry main` finds both the C `main` that starts the Rust runtime and the
real Rust `main`, so you never type a mangled name. A `staticlib` harness has
no `main`; pass `--entry LLVMFuzzerTestOneInput`. `fuzz_target!` means
`LLVMFuzzerTestOneInput` and `rust_fuzzer_test_input`.

Tests: `test_analyzer_core.py::test_rust_entry_rooting`,
`::test_entry_main_resolves_rust_and_c`, `::test_entry_fuzz_target_alias`, and
`test_reachable_sets.py::test_rust_main_entry` and `::test_rust_only_entry_rooting`.
