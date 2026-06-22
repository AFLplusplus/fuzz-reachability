# Static Fuzz-Reachability Analyzer (C / C++ / Rust)

Given a project's build, this tool computes the set of functions a fuzz entry
point (`LLVMFuzzerTestOneInput`, a Rust cargo-fuzz target, or any entry you name)
can **statically reach**. It works uniformly across C, C++, and Rust — including
mixed-language projects — by analyzing merged LLVM bitcode.

The result is a **sound-leaning over-approximation**: it answers *which functions
can be reached*, not which ones ran. No function that is actually reachable is
ever reported unreachable. Over-reporting is expected and safe; under-reporting
is a bug.

Feed the output to AFL++ or clang's SanitizerCoverage allow/ignore lists to instrument
only reachable code — cheaper, more focused fuzzing:
- **AFL++**: `export AFL_LLVM_ALLOWLIST=$(pwd)/reached.txt` -or- `export AFL_LLVM_DENYLIST=$(pwd)/not_reached.txt`
- **sancov based fuzzers** (libfuzzer, honggfuzz, libafl, AFL++): `-fsanitize-coverage-allowlist=$(pwd)/reached.txt` -or- `-fsanitize-coverage-ignorelist=$(pwd)/not_reached.txt`

**Recommendation:** use the allow feature with `reached.txt` rather than the deny/ignore feature.

Additionally you can feed the output files into [cov-analysis](github.com/AFLplusplus/cov-analysis) - the state-of-the-art coverage analysis tooling.

**Deep dives:**
- Worked examples, step by step — a generic `LLVMFuzzerTestOneInput` harness for AFL++/libfuzzer (libxml2), a ziggy harness (the `url` crate), and cargo-afl harnesses (cpp_demangle and rustyknife) — [`docs/EXAMPLES.md`](docs/EXAMPLES.md)
- LLVM version support and the SVF fallback plan — [`docs/llvm-support.md`](docs/llvm-support.md)


Author: Marc "vanHauser" Heuse

License: GNU AGPL v3 or newer

## How it works

```
 driver (Python)              analyzer (C++ / LLVM)
 ───────────────              ─────────────────────
   acquire bitcode ─┐
   C/C++ : gllvm    │   llvm-link    load .bc → build call graph →
   Rust  : rustc    ├─► merge .bc ─► resolve indirect calls → BFS from
   --emit=llvm-bc  ─┘                entry → JSON report + sancov lists
```

Two components, joined by merged bitcode:

- **Driver** (Python) — acquires bitcode per language, merges it with
  `llvm-link`, verifies the LLVM toolchain is version-coherent, and runs the
  analyzer.
- **Analyzer** (C++ linking LLVM) — loads the merged `.bc`, builds the call
  graph, resolves indirect calls (C function pointers, C++ virtual dispatch, Rust
  `dyn`/`fn` pointers), treats function pointers that escape to code outside the
  bitcode (handed to an external/indirect call or returned — e.g. qsort/bsearch
  comparators, atexit/pthread/`std::call_once` callbacks) as reachable, computes
  reachability from the entry, and emits a JSON report plus the two sancov lists.
  It demangles C++ (Itanium) and Rust names.

## Prerequisites

- **LLVM ≥ 21.** One coherent toolchain: `clang`, `clang++`, `llvm-link`, `opt`,
  and the analyzer all share one major **M ≥ 21**, and rustc's LLVM is no newer
  than M. See [`docs/llvm-support.md`](docs/llvm-support.md).
  **NOTE!** especially as a Rust user, we recommend to install LLVM via
  https://apt.llvm.org/llvm.sh instead of the distribution, as those will be outdated!
- **Go** (to install `gllvm`), **Python ≥ 3.12**, and a **C++17** compiler. Rust
  targets also need **rustc / cargo** (nightly, but one using LLVM 21 or prior).

## Install

The analyzer builds with a plain Makefile driven by `llvm-config` — no CMake.

```bash
bash scripts/setup.sh        # gllvm + rust-src, create .venv, build the analyzer
```

Or piecemeal:

```bash
make venv                    # create .venv (driver, editable + pytest)
make build                   # build the analyzer on the auto-selected LLVM (≥ 21)
make build LLVM_MAJOR=23     # ...or pin a specific major
make test                    # run the full test suite
make matrix                  # build + test against every installed LLVM ≥ 21
make help                    # list all targets
```

To run the CLI, point it at the built analyzer and put `gllvm` on `PATH`:

```bash
export REACHABILITY_ANALYZER=$PWD/analyzer/build/reachability-analyzer
export PATH="$(go env GOPATH)/bin:$PATH"     # gclang / gclang++ / get-bc
source .venv/bin/activate                    # or call .venv/bin/reachability directly
reachability check-toolchain                 # verify LLVM version coherence first
```

## Quick start

```bash
reachability run --lang <target> --project <dir> --out <file>
```

`<target>` is a source language (`c`, `cpp`, `rust`, `mixed`) or a Rust fuzz
harness (`libfuzzer`, `ziggy`, `afl`). Each sets a default entry point, so the
common case needs no `--entry`. The build command and the artifact are
auto-detected for C/C++; override them with `--build-cmd` / `--artifact` when
needed.

Full options: [Command-line reference](#command-line-reference).

## Examples

### A C target

`fixtures/c_direct` is a small C fuzz target. Its build and artifact are
auto-detected:

```bash
reachability run --lang c --project fixtures/c_direct --out c.json -v
```

```
reachable 3 / defined 4  (0 indirect-only, 1 unreachable)  [backend=type-based]
```

`LLVMFuzzerTestOneInput → used_a → used_b` are reachable; `dead_fn` lands in
`unreachable_defined`.

### A C++ target (CMake)

`examples/cpp_cmake` uses virtual dispatch. The driver detects the CMake build,
wraps it with `gllvm`, and analyzes the resulting executable:

```bash
reachability run --lang cpp --project examples/cpp_cmake --out cpp.json -v
```

The virtual call `Codec::decode` over-approximates to **both** overrides, reached
via indirect edges:

```
  Raw::decode(unsigned char const*, unsigned long) | via indirect
  Xor::decode(unsigned char const*, unsigned long) | via indirect
```

### A Rust target

`fixtures/rust_dyn` is a Rust `staticlib` whose `LLVMFuzzerTestOneInput`
dispatches through a `dyn Trait`. The driver builds it with
`RUSTFLAGS="--emit=llvm-bc …"` and collects the per-crate bitcode:

```bash
reachability run --lang rust --project fixtures/rust_dyn --out rust.json -v
```

The trait-object call resolves to both implementations, via indirect edges:

```
  <rust_dyn::Inc as rust_dyn::Op>::run | via indirect
  <rust_dyn::Dbl as rust_dyn::Op>::run | via indirect
```

### A mixed C + Rust target (cargo-fuzz shape)

`fixtures/mixed_c_rust` has C++ glue calling an `extern "C"` Rust entry. Use
`--lang mixed`; the driver builds and merges both sides' bitcode (gllvm for the
glue, cargo for Rust), and the cross-language edge resolves by C-ABI symbol name:

```bash
reachability run --lang mixed --project fixtures/mixed_c_rust \
  --artifact glue.o --out mixed.json -v
```

Point `--artifact` at the C/C++ object so it is picked out from the Rust build
outputs.

### A target that links a static library

A tool linked against a static library (say `tools/thumbnail` linking
`libtiff.a`) embeds only the archive members the linker actually pulled in. To
analyze the **whole library** — not just the slice the linker kept — point
`--artifact` at the linked binary and keep the default `--static-libs auto`:

```bash
reachability run --lang c --project tiff-4.0.4 --artifact tools/thumbnail \
  --out tiff.json -v
```

The driver merges `thumbnail`'s own objects with the full contents of
`libtiff.a`. Functions in members the linker discarded (e.g. `TIFFReadRGBAImage`,
`TIFFPrintDirectory`) now show up as unreachable instead of vanishing, while the
reachable set is unchanged from the linker's view — adding the rest of the
library can only add *unreachable* functions, never remove reachable ones. Use
`--static-libs none` for the linker's view only, or `all` to include every
bitcode archive in the tree.

### A ziggy harness

A [ziggy](https://github.com/srlabs/ziggy) harness is a Rust binary whose fuzz
loop lives in `main` rather than in `LLVMFuzzerTestOneInput`. `--lang ziggy`
acquires the bitcode and roots at `main` automatically:

```bash
reachability run --lang ziggy --project <harness> --out z.json
```

> For complete, start-to-finish walkthroughs on real targets — ziggy (the `url`
> crate), cargo-afl (cpp_demangle and rustyknife), and libFuzzer (libxml2)
> harnesses — see [`docs/EXAMPLES.md`](docs/EXAMPLES.md).

## Command-line reference

The `reachability` CLI has two subcommands.

### `reachability check-toolchain`

Resolves and validates the LLVM toolchain (analyzer, `clang`/`clang++`,
`llvm-link`, `opt`, rustc) for version coherence and prints what it found. Run it
first; it exits non-zero on any incoherence. See
[`docs/llvm-support.md`](docs/llvm-support.md) for the policy.

### `reachability run`

Builds a project, merges its bitcode, and computes the reachable set from the
entry point(s).

| Option | Default | Meaning |
|--------|---------|---------|
| `--project DIR` | *(required)* | Project directory to build and analyze. |
| `--lang TARGET` | *(required)* | Target type (see the table below): sets how bitcode is acquired and the default entry. |
| `--out FILE` | *(required)* | Path for the JSON report. The two sancov lists default to `reached.txt` / `not_reached.txt` beside it. |
| `--entry NAME` | per `--lang` | Entry to root reachability at. **Repeatable**; overrides the target default. See [Entry resolution](#entry-resolution). |
| `--backend {type-based,svf}` | `type-based` | Indirect-call resolution backend. `svf` needs an SVF-enabled analyzer: run `make build-svf` and `--backend=svf` picks up `analyzer/build-svf/reachability-analyzer` automatically (override with `$REACHABILITY_ANALYZER_SVF`). |
| `--artifact PATH` | auto-detect | C/C++ only: the built binary/object/archive to extract bitcode from (relative to `--project`). Auto-detected otherwise, preferring an executable over a shared library, archive, then object. |
| `--build-cmd CMD` | auto-detect | C/C++ only: shell build command, run with `gllvm` injected. E.g. `"cmake -S . -B build && cmake --build build"`. Auto-detected from the project files otherwise (`configure` → `Makefile` → `CMakeLists.txt` → `build.ninja` → `meson.build`, else `make`). |
| `--static-libs {auto,none,all}` | `auto` | C/C++ only: how to treat static archives (`.a`) the target links. `auto` also analyzes each linked archive in full, so members the linker dropped are reported rather than silently absent. `none` keeps only the linker's view. `all` includes every bitcode archive in the tree, skipping any whose members another archive already covers and resolving residual overlaps at link time (`llvm-link --override`). |
| `--build-std` | off | Rust only: build the standard library from source (`-Zbuild-std`) so std functions appear in the graph instead of as external declarations. |
| `--dot FILE` | *(none)* | Also write the reachable subgraph as Graphviz DOT (indirect edges dashed/red). |
| `--reached FILE` | beside `--out` | Path for the sancov **allowlist** of reachable functions. |
| `--not-reached FILE` | beside `--out` | Path for the sancov **ignorelist** of unreachable functions. |
| `-v`, `--verbose` | off | Narrate each pipeline stage (toolchain → build → merge → analyze): echoes the tool commands run, streams the build output live, and lists the collected bitcode modules. |

#### Target types (`--lang`)

| `--lang` | acquires via | default entry |
|----------|--------------|---------------|
| `c` | gllvm (`gclang`) | `main` + `LLVMFuzzerTestOneInput` |
| `cpp` | gllvm (`gclang++`) | `main` + `LLVMFuzzerTestOneInput` |
| `rust` | `cargo` + `--emit=llvm-bc` | `main` |
| `mixed` | gllvm **and** cargo (merged) | `LLVMFuzzerTestOneInput` |
| `libfuzzer` | cargo (Rust) | `fuzz_target!` |
| `ziggy` | cargo (Rust) | `main` |
| `afl` | cargo (Rust) | `main` |

The C/C++ targets root at both `main` and `LLVMFuzzerTestOneInput`, so one
`--lang c`/`cpp` covers a normal program and an LLVMFUzzerTestOneInput harness alike. A default
entry that matches nothing is a harmless warning, because roots are unioned.

#### Entry resolution

`--entry` never requires a mangled symbol. A token matches — unioned across all
of:

- an exact mangled symbol (e.g. `_Z3foov`),
- an exact demangled name (e.g. `foo()`),
- a demangled `::name` suffix (so `main` finds `crate::main`), and
- the alias `fuzz_target!` (→ `LLVMFuzzerTestOneInput` + `rust_fuzzer_test_input`).

Matching more than one function only adds roots, which stays sound. For a Rust
binary, just root at `main`: the token matches the real Rust `main`, so you never
need to type a mangled symbol.

#### Environment variables

| Variable | Purpose |
|----------|---------|
| `REACHABILITY_ANALYZER` | Path to the analyzer binary (default `analyzer/build/reachability-analyzer`). |
| `REACHABILITY_ANALYZER_SVF` | Path to an SVF-enabled analyzer (for `--backend=svf` and tests). |
| `CLANG` / `CLANGXX` / `LLVM_LINK` / `OPT` | Override individual tool paths (otherwise resolved by major from the analyzer's LLVM). |
| `PATH` | Must contain `gclang` / `gclang++` / `get-bc` (gllvm) for C/C++/mixed targets. |

## Output

`reachability run` writes three files:

- **`<out>.json`** — `summary` counts, a `reachable` array (mangled and demangled
  name, source file/line when debug info is present, `via` =
  `direct`/`indirect`/`both`, and an `indirect_only` flag), and an
  `unreachable_defined` array. With `--dot FILE`, also the reachable subgraph.
- **`reached.txt`** — a SanitizerCoverage **allowlist** of reachable functions.
- **`not_reached.txt`** — a SanitizerCoverage **ignorelist** of unreachable
  functions.

Both lists use each function's **mangled** (LLVM symbol) name — what clang
matches `fun:` against — so they cover C, C++, and Rust. Feed either to clang to
instrument only reachable code:

```bash
# instrument ONLY reachable functions:
clang -fsanitize-coverage=trace-pc-guard -fsanitize-coverage-allowlist=reached.txt ...
# OR: instrument everything EXCEPT unreachable functions:
clang -fsanitize-coverage=trace-pc-guard -fsanitize-coverage-ignorelist=not_reached.txt ...
```

> A coverage allowlist instruments a function only when both a `src:` and a
> `fun:` entry match, so `reached.txt` opens with a `src:*` line. An ignorelist
> has no such requirement, so `not_reached.txt` is pure `fun:` lines. (Verified
> against clang in `driver/tests/test_covlists.py`.)

## Indirect-call backends

| Backend | Flag | Notes |
|---------|------|-------|
| Type-based (default) | `--backend=type-based` | Buckets address-taken functions by LLVM function type. Language-agnostic, always available, sound. |
| SVF Andersen points-to | `--backend=svf` | Higher precision. Optional; built separately. Falls back to type-based for any callsite it cannot resolve. |

SVF currently builds against **LLVM 21 only**. On LLVM 22/23 the default
type-based backend is fully functional, and `--backend=svf` returns a clear error
rather than degrading silently. Build SVF with:

```bash
make build-svf      # builds the SVF dependency + an SVF-enabled analyzer (pins LLVM 21)
```

This writes `analyzer/build-svf/reachability-analyzer`; `--backend=svf` finds it
automatically (no env var needed). Setting `$REACHABILITY_ANALYZER` selects the
type-based binary only — the SVF binary is selected via `$REACHABILITY_ANALYZER_SVF`.

See [`docs/llvm-support.md`](docs/llvm-support.md) for the compatibility matrix
and the full fallback plan.

## Testing

```bash
make test       # full pytest suite (analyzer .ll goldens + per-language soundness)
make matrix     # LLVM version-compatibility matrix (catches future-LLVM breakage)
```

Each fixture in `fixtures/` carries a `must_reach` / `must_not_reach` set; every
backend must satisfy the soundness invariant on each.

## Project layout

```
analyzer/   C++ analyzer + Makefile (src/, built via llvm-config)
driver/     Python driver (toolchain, acquire_*, link, analyze, cli)
fixtures/   per-language test targets with expected reachable sets
examples/   worked examples (cpp_cmake/)
scripts/    setup.sh, build_svf.sh, test_matrix.sh, select_llvm.sh
docs/        worked examples (EXAMPLES.md), LLVM support, SVF notes
```

## Limitations

This is a static over-approximation, not dynamic coverage. Its precision is
bounded by indirect-call resolution and by any missing bitcode — precompiled
libraries, or the Rust standard library without `--build-std`.
