# Static Fuzz-Reachability Analyzer (C / C++ / Rust, LLVM IR level)

Given a project's build, compute the set of functions **statically reachable**
from a fuzz entry point (`LLVMFuzzerTestOneInput`, or a Rust cargo-fuzz entry).
The result is a sound-leaning **over-approximation** — "which functions *can* be
reached" — not dynamic coverage. It works uniformly for C, C++, and Rust
(including mixed-language projects) by analyzing merged LLVM bitcode.

**Soundness invariant:** no function that is actually reachable is ever reported
unreachable. Over-approximation is expected; under-approximation is a bug.

- **LLVM version support + SVF fallback plan:** [`docs/llvm-support.md`](docs/llvm-support.md)
- **ziggy fuzz harness (Rust `main` entry):** [`docs/ziggy.md`](docs/ziggy.md)

Author: Marc "vanHauser" Heuse

License: GNU Affero General Public License 3 or newer

## How it works

```
 driver (Python)            analyzer (C++ / LLVM)
 ───────────────            ─────────────────────
   acquire bitcode ─┐
   C/C++ : gllvm    │   llvm-link     load .bc → call graph → indirect resolve
   Rust  : rustc    ├─► merge .bc ─►  (type-based | SVF) → BFS from entry →
   --emit=llvm-bc  ─┘                 JSON report (+ optional DOT)
```

- **Analyzer** — a standalone C++ tool linking LLVM. Loads merged `.bc`, builds
  the call graph, resolves indirect calls (C function pointers, C++ virtual
  dispatch, Rust `dyn`/`fn` pointers), runs reachability, emits JSON. Demangles
  Itanium (C++) and Rust legacy/v0 names.
- **Driver** — a Python orchestrator that acquires bitcode per language, merges
  it, checks toolchain version coherence, and runs the analyzer.

## Prerequisites

- **LLVM ≥ 21** (21 is the floor; 22, 23, … work for the core analyzer). A
  coherent toolchain: `clang`, `clang++`, `llvm-link`, `opt`, and the analyzer
  all on one major **M ≥ 21**, with rustc's LLVM ≤ M. See
  [`docs/llvm-support.md`](docs/llvm-support.md).
- **Go** (to install `gllvm`), **Python ≥ 3.12**, **rustc/cargo** (nightly for
  Rust targets), a C++17 compiler.

## Build

The analyzer builds with a plain Makefile driven by `llvm-config`.

```bash
bash scripts/setup.sh          # installs gllvm + rust-src, creates .venv, builds the analyzer
# or, piecemeal:
make venv                      # create .venv with the driver + pytest (scripts/setup_venv.sh)
make build                     # build analyzer on LLVM 21  -> analyzer/build/reachability-analyzer
make build LLVM_MAJOR=23       # build against LLVM 23 instead
make test                      # run the full test suite (auto-creates .venv if missing)
make matrix                    # build + test against every installed LLVM (21/22/23/…)
```

The Python driver lives in a virtualenv at `.venv` (created by
`make venv` / `scripts/setup_venv.sh`, which installs `driver/` editable plus
its test deps). To use the `reachability` CLI directly, point it at the built
analyzer and put `gllvm` on `PATH`:

```bash
export REACHABILITY_ANALYZER=$PWD/analyzer/build/reachability-analyzer
export PATH="$(go env GOPATH)/bin:$PATH"     # gclang / gclang++ / get-bc
source .venv/bin/activate                    # or call .venv/bin/reachability directly
reachability check-toolchain                 # verifies LLVM version coherence
```

`make help` lists all targets.

## Running it

The driver subcommand is `reachability run --lang TARGET --project DIR --out FILE`,
where `TARGET` is a source language (`c`/`cpp`/`rust`/`mixed`) or a Rust fuzz
harness (`libfuzzer`/`ziggy`/`afl`). Each target sets a default entry point, so
the common case needs no `--entry` at all. Full flag reference:
[Command-line reference](#command-line-reference).

### 1. A simple C target

`fixtures/c_direct` is a tiny C fuzz target (its `Makefile` compiles `main.c` to
an object; a fuzz target has no `main()`, so `get-bc` extracts the object's
bitcode):

```bash
reachability run --lang c --project fixtures/c_direct --artifact main.o --out c.json -v
```

```
reachable 3 / defined 4  (0 indirect-only, 1 unreachable)  [backend=type-based]
```

`LLVMFuzzerTestOneInput → used_a → used_b` are reachable; `dead_fn` is reported
in `unreachable_defined`.

### 2. A CMake C++ target

`examples/cpp_cmake` is a C++ fuzz target built with CMake and using virtual
dispatch. `gllvm` wraps the CMake build (the driver injects `CC=gclang`,
`CXX=gclang++`), and `get-bc` extracts whole-program bitcode from the executable:

```bash
reachability run --lang cpp --project examples/cpp_cmake \
  --build-cmd "cmake -S . -B build && cmake --build build" \
  --artifact build/fuzz_target --out cpp.json -v
```

The virtual call `Codec::decode` resolves (over-approximated) to **both**
overrides, flagged as reached via indirect edges:

```
  Raw::decode(unsigned char const*, unsigned long) | via indirect
  Xor::decode(unsigned char const*, unsigned long) | via indirect
```

### 3. A Rust target

`fixtures/rust_dyn` is a Rust `staticlib` with a `#[no_mangle]`
`LLVMFuzzerTestOneInput` that dispatches through a `dyn Trait`. The driver builds
it with `RUSTFLAGS="--emit=llvm-bc …"` and collects the per-crate bitcode:

```bash
reachability run --lang rust --project fixtures/rust_dyn --out rust.json -v
```

The trait-object call resolves to both impls (v0-demangled), via indirect edges:

```
  <rust_dyn::Inc as rust_dyn::Op>::run | via indirect
  <rust_dyn::Dbl as rust_dyn::Op>::run | via indirect
```

> **Mixed C+Rust (cargo-fuzz shape):** `fixtures/mixed_c_rust` shows a C++
> `LLVMFuzzerTestOneInput` glue calling an `extern "C"` Rust entry. Use
> `--lang mixed`; the cross-language edge resolves by C ABI symbol name once both
> sides' bitcode is merged.

### 4. A ziggy harness (Rust `main` entry)

A [ziggy](https://github.com/srlabs/ziggy) harness is a Rust **bin** crate whose
fuzz loop lives in `ziggy::fuzz!(|data| { … })` inside `fn main()` — so the entry
is the **Rust `main`**, not `LLVMFuzzerTestOneInput`. Root the analysis at the
*mangled* Rust `main` (the bare `main` is a C-ABI shim that dead-ends in
precompiled `std`):

```bash
cd <harness>
RUSTFLAGS="--emit=llvm-bc -Cembed-bitcode=yes -Ccodegen-units=1" cargo build
llvm-link-22 target/debug/deps/*.bc -o merged.bc
main=$(llvm-nm-22 --defined-only target/debug/deps/<bin>-*.bc | grep ' T ' | grep main)  # pick <crate>::main
reachability-analyzer merged.bc --entry "$main" --out reach.json \
  --reached-out reached.txt --not-reached-out not_reached.txt
```

Full walkthrough, gotchas (custom `rustflags`, workspace target dirs, finding the
symbol), and a worked move-smith example: [`docs/ziggy.md`](docs/ziggy.md).

## Command-line reference

The `reachability` CLI has two subcommands.

### `reachability check-toolchain`

Resolves and validates the LLVM toolchain (analyzer, `clang`/`clang++`,
`llvm-link`, `opt`, rustc) for version coherence and prints what it found. Takes
no options. Exits non-zero on any incoherence. Run it first. See
[`docs/llvm-support.md`](docs/llvm-support.md) for the version policy.

### `reachability run`

Build a project, merge its bitcode, and compute the reachable set from the entry
point(s).

| Option | Default | Meaning |
|--------|---------|---------|
| `--project DIR` | *(required)* | Project directory to build and analyze. |
| `--lang TARGET` | *(required)* | Target type — see the table below. Selects how bitcode is acquired and the default entry. |
| `--out FILE` | *(required)* | Path for the JSON report. The two sancov lists default to `reached.txt` / `not_reached.txt` next to it. |
| `--entry NAME` | per `--lang` | Entry function to root reachability at. **Repeatable.** Overrides the target default. Accepts a mangled symbol, a demangled name, a `::name` suffix (e.g. `main`), or the alias `fuzz_target!` — no mangled name required. |
| `--backend {type-based,svf}` | `type-based` | Indirect-call resolution backend. `svf` needs an SVF-enabled analyzer (`make build-svf`). |
| `--artifact PATH` | `main.o` | C/C++ only: the built object/binary `get-bc` extracts whole-program bitcode from (relative to `--project`). |
| `--build-cmd CMD` | `make` | C/C++ only: shell build command, run with gllvm wrappers injected. E.g. `"cmake -S . -B build && cmake --build build"`. |
| `--build-std` | off | Rust only: build the standard library from source too (`-Zbuild-std`), so std functions appear in the graph instead of as external declarations. |
| `--dot FILE` | *(none)* | Also write the reachable subgraph as Graphviz DOT (indirect edges dashed/red). |
| `--reached FILE` | `reached.txt` next to `--out` | Path for the sancov **allowlist** of reachable functions. |
| `--not-reached FILE` | `not_reached.txt` next to `--out` | Path for the sancov **ignorelist** of unreachable functions. |
| `-v`, `--verbose` | off | Print a per-function breakdown in the summary. |

**Target types (`--lang`)** — each maps to a bitcode-acquisition method and a
default entry:

| `--lang` | acquires via | default entry |
|----------|--------------|---------------|
| `c` | gllvm (`gclang`) | `LLVMFuzzerTestOneInput` |
| `cpp` | gllvm (`gclang++`) | `LLVMFuzzerTestOneInput` |
| `rust` | `cargo` + `--emit=llvm-bc` | `LLVMFuzzerTestOneInput` |
| `mixed` | gllvm **and** cargo (merged) | `LLVMFuzzerTestOneInput` |
| `libfuzzer` | cargo (Rust) | `fuzz_target!` (→ `LLVMFuzzerTestOneInput` + `rust_fuzzer_test_input`) |
| `ziggy` | cargo (Rust) | `main` |
| `afl` | cargo (Rust) | `main` |

The harness targets (`libfuzzer`/`ziggy`/`afl`) are the Rust shapes; a C/C++
libFuzzer or afl harness uses `--lang c`/`cpp` (default `LLVMFuzzerTestOneInput`,
or `--entry main` for a C afl harness).

**Entry resolution.** `--entry` never requires a mangled symbol. A token matches,
unioned across all of: an exact mangled symbol; an exact demangled name; a
demangled `::token` suffix (so `main` finds `crate::main`, and the Rust legacy
`::h<hash>` disambiguator is ignored); and the alias `fuzz_target!`. Matching more
than one function just adds roots — always sound (over-approximating). For a Rust
binary, root at `main` (resolves the real Rust `main`), not the bare C-ABI `main`
shim which dead-ends in precompiled `std`.

**Environment variables.**

| Variable | Purpose |
|----------|---------|
| `REACHABILITY_ANALYZER` | Path to the analyzer binary (default `analyzer/build/reachability-analyzer`). |
| `REACHABILITY_ANALYZER_SVF` | Path to an SVF-enabled analyzer (used by `--backend=svf`/tests). |
| `CLANG` / `CLANGXX` / `LLVM_LINK` / `OPT` | Override individual tool paths (otherwise resolved by major from the analyzer's LLVM). |
| `PATH` | Must contain `gclang`/`gclang++`/`get-bc` (gllvm) for C/C++/mixed targets. |

## Output

`reachability run` writes three files (the analyzer flags are
`--out` / `--reached-out` / `--not-reached-out`):

- **`<out>.json`** — `summary` counts, a `reachable` array (mangled + demangled
  name, source file/line when debug info is present, `via` =
  `direct`/`indirect`/`both`, and an `indirect_only` audit flag), and an
  `unreachable_defined` array. `--dot FILE` additionally writes the reachable
  subgraph (indirect edges dashed/red).
- **`reached.txt`** — a SanitizerCoverage **allowlist** of the statically
  reachable functions, one `fun:<mangled>` per line.
- **`not_reached.txt`** — a SanitizerCoverage **ignorelist** of the unreachable
  functions, one `fun:<mangled>` per line.

Both lists use the function's **mangled** (LLVM symbol) name — what clang matches
`fun:` against — so they work for C, C++ (Itanium), and Rust (v0). Feed them to
clang to instrument only reachable code (cheaper, more focused fuzzing):

```bash
# instrument ONLY reachable functions:
clang -fsanitize-coverage=trace-pc-guard -fsanitize-coverage-allowlist=reached.txt ...
# OR: instrument everything EXCEPT unreachable functions:
clang -fsanitize-coverage=trace-pc-guard -fsanitize-coverage-ignorelist=not_reached.txt ...
```

> A coverage **allowlist** instruments a function only when both a `src:` and a
> `fun:` entry match, so `reached.txt` begins with a `src:*` line. An
> **ignorelist** has no such requirement, so `not_reached.txt` is pure `fun:`
> lines. (Verified against clang in `driver/tests/test_covlists.py`.)

## Indirect-call backends

| Backend | Flag | Notes |
|---------|------|-------|
| Type-based (default) | `--backend=type-based` | Address-taken functions bucketed by LLVM function type. Language-agnostic, always available, sound. |
| SVF Andersen points-to | `--backend=svf` | Higher precision. Optional; built separately. Falls back to type-based for callsites it cannot resolve. |

SVF currently builds against **LLVM 21 only** (upstream targets 21.1.x). On
LLVM 22/23 the core type-based backend is fully functional and `--backend=svf`
returns a clear error rather than degrading silently. Build it with:

```bash
make build-svf                  # builds the SVF dependency + an SVF-enabled analyzer
```

`make build-svf` pins LLVM 21 (the only version SVF builds against) regardless of
the auto-selected core default; pass `LLVM_MAJOR=<n>` to force a different one.

See [`docs/llvm-support.md`](docs/llvm-support.md) for the compatibility matrix
and the full fallback plan.

## Testing

```bash
make test       # full pytest suite (analyzer .ll goldens + per-language soundness)
make matrix     # LLVM version-compatibility matrix (detects future-LLVM breakage)
```

Fixtures in `fixtures/` carry a `must_reach` / `must_not_reach` set; every
backend must satisfy the soundness invariant on each.

## Project layout

```
analyzer/       C++ analyzer + Makefile (src/, build via llvm-config)
driver/         Python driver (reachability/: toolchain, acquire_*, link, analyze, cli)
fixtures/       per-language test targets with expected reachable sets
examples/       worked examples (cpp_cmake/)
scripts/        setup.sh, build_svf.sh, test_matrix.sh
docs/           LLVM support + SVF fallback, SVF build notes
```

## Non-goals

Static over-approximation, not dynamic coverage. Soundness is bounded by
indirect-call precision and by missing bitcode (precompiled libraries, Rust std
without `--build-std`).
