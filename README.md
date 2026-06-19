# Static Fuzz-Reachability Analyzer (C / C++ / Rust, LLVM IR level)

Given a project's build, compute the set of functions **statically reachable**
from a fuzz entry point (`LLVMFuzzerTestOneInput`, or a Rust cargo-fuzz entry).
The result is a sound-leaning **over-approximation** — "which functions *can* be
reached" — not dynamic coverage. It works uniformly for C, C++, and Rust
(including mixed-language projects) by analyzing merged LLVM bitcode.

**Soundness invariant:** no function that is actually reachable is ever reported
unreachable. Over-approximation is expected; under-approximation is a bug.

- **LLVM version support + SVF fallback plan:** [`docs/llvm-support.md`](docs/llvm-support.md)

Author: Marc "vanHauser" Heuse
License: GNU Affero General Public License 3 or newer

## How it works

```
 driver (Python)            analyzer (C++ / LLVM)
 ───────────────            ─────────────────────
 acquire bitcode  ─┐
   C/C++ : gllvm    │  llvm-link    load .bc → call graph → indirect resolve
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

## Build (no CMake)

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

The driver subcommand is `reachability run --lang {c,cpp,rust,mixed} --project DIR --out FILE`.
The default entry is `LLVMFuzzerTestOneInput` (override with `--entry`, repeatable).

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
make build-svf LLVM_MAJOR=21    # builds the SVF dependency + an SVF-enabled analyzer
```

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
