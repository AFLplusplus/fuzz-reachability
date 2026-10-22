# Development

## Setup

```bash
bash scripts/setup.sh     # gllvm 1.3.1, optional rust-src, .venv, analyzer build
```

`scripts/setup.sh` installs gllvm with `go install` (with
`REACHABILITY_CAPTURE=rllvm`, rllvm 0.7.1 or newer with `brew install`, or
`cargo install` without Homebrew, unless a new enough one is on `PATH`), adds
the `rust-src` component when `rustup` is available, creates `.venv` with the
driver (editable) and the pinned test dependencies from `requirements-dev.txt`,
and builds the analyzer. It does not upgrade pip. `make venv` only creates the
virtual environment.

## Building the analyzer

The analyzer is built with a plain Makefile driven by `llvm-config`; there is
no CMake.

```bash
make build                   # newest installed LLVM >= 21
make build LLVM_MAJOR=23     # a specific LLVM
make clean
```

`scripts/select_llvm.sh` picks the newest `llvm-config-N` with `N >= 21`.
If no supported versioned executable is present, it accepts an unversioned
`llvm-config` reporting LLVM 21 or newer. `LLVM_CONFIG` can select an exact
executable for `scripts/setup.sh` or `make build`. Newer LLVM reads older
bitcode but not newer, so the newest is the safest default.

The analyzer uses `clang++` from `llvm-config --bindir` when present. If LLVM
and Clang are separate packages, as on NixOS, it looks for `clang++-M` and then
`clang++` on `PATH`, and checks that the compiler's major version matches LLVM
major `M`. Set `CXX` explicitly to a matching compiler if needed.

To keep several builds side by side, build into a version directory:

```bash
make -C analyzer LLVM_CONFIG=llvm-config-22 BUILD=build/22
export REACHABILITY_ANALYZER="$PWD/analyzer/build/22/reachability-analyzer"
```

`make -C analyzer` defaults to `llvm-config-21`; use the top-level `make
build` to get the automatic choice. The build compiles
`dangerous_functions.txt` into the analyzer
(`scripts/gen_dangerous_list.sh`), so rebuild after editing it.

## Tests

```bash
make test        # build the analyzer, then run driver/tests with pytest
```

`make test` puts `$(go env GOPATH)/bin` on `PATH` and points
`REACHABILITY_ANALYZER` at `analyzer/build/`. Tests skip themselves when a tool
they need (gllvm, cargo, cargo-ziggy, the AFL++ plugins) is missing.

| File | What it covers |
|---|---|
| `test_analyzer_core.py` | The analyzer on the hand-written modules in `analyzer/test/*.ll`; no build tools needed |
| `test_soundness_property.py` | Random modules compared against an oracle |
| `test_reachable_sets.py` | Builds each fixture and checks its `expected.json` |
| `test_cli.py` | The `reachability` command, output files, `--clean`, end-to-end runs |
| `test_covlists.py` | The lists, checked against clang's own allowlist and ignorelist handling |
| `test_acquire_c.py`, `test_acquire_rust.py` | Build detection, flags and bitcode collection |
| `test_rust_hash_stability.py`, `test_v0_mangling_probe.py` | Rust names across builds and mangling schemes |
| `test_toolchain.py`, `test_link.py`, `test_analyze.py`, `test_report.py`, `test_diagnostics.py` | The other driver modules |

### Adding an analyzer test

1. Write a small module, `analyzer/test/<name>.ll`. Hand-written IR is fine;
   for C, start from
   `clang-23 -O0 -Xclang -disable-llvm-passes -fno-inline
   -fno-inline-functions -g -emit-llvm -S x.c`.
2. Add a test to `driver/tests/test_analyzer_core.py` that runs the analyzer on
   `ll("<name>.ll")` through the `run_analyzer` fixture and checks the JSON.
3. Make sure the test fails without your analyzer change.

### Adding a fixture

A fixture is a small project in `fixtures/<name>/` with its build files and an
`expected.json`:

```json
{
  "must_reach": ["LLVMFuzzerTestOneInput", "used_a", "used_b"],
  "must_not_reach": ["dead_fn"]
}
```

`must_reach` must be a subset of the reachable set; `must_not_reach` guards
against the over-approximation swallowing everything. A name matches the
mangled or demangled name, or its last `::` part. Add a test to
`driver/tests/test_reachable_sets.py` that builds the fixture and calls
`assert_soundness`. A Rust fixture's `.cargo/config.toml` must sit at the
fixture's root, because cargo looks for it from the project directory upwards.

### Comparing analyzer changes

There is no snapshot script in the repository yet ([TODO.md](../TODO.md) T2).
Until then, compare a change by hand: build the old and new analyzer, and for
every module in `analyzer/test/` and the fixtures' merged bitcode record the
sorted `--dump-edges` output and, with each defined function as the only
entry, the JSON report and both lists. Then diff the two sets of files. Only
the intended outputs should change.

## LLVM matrix

```bash
make matrix
```

`scripts/test_matrix.sh` builds and tests the analyzer against every
`llvm-config-N` with `N >= 21` on `PATH`, each in `analyzer/build/N`. If none
is present, it tries an unversioned `llvm-config`. It fails if any build or
test fails. It also fails when it finds none; `MATRIX_ALLOW_EMPTY=1`
turns that into a skip for optional local runs. Results are in
[llvm-support.md](llvm-support.md).

## Static analysis and clangd

```bash
make compdb            # analyzer/compile_commands.json for clangd
make static-analysis   # cppcheck and the Clang Static Analyzer
```

## Cross-repository check

```bash
make ci
```

`scripts/ci_cross_repo.sh` runs `make test` and then the test suite of
[cov-analysis](https://github.com/AFLplusplus/cov-analysis), which parses this
tool's output and computes `key` the same way. A change to that contract then
fails in both repositories. It expects cov-analysis next to this checkout, or
at `COV_ANALYSIS_DIR`. `SKIP_CROSS_REPO=1` skips that part on purpose.
The C++ end-to-end test also runs `reachability verify` against the real
corpus replay's `coverage.json` and requires zero contradictions.

## Continuous integration

`.github/workflows/tests.yml` runs on every push to `main` and every pull
request, on Ubuntu 24.04:

| LLVM | Python | Steps |
|---|---|---|
| 21 | 3.10 | `make test`, `make matrix` |
| 22 | 3.12 | `make test`, `make matrix` |
| 23 | 3.12 | `make test`, `make matrix`, the end-to-end C, Rust and mixed CLI tests, `make compdb static-analysis` |

Each job installs LLVM from apt.llvm.org, gllvm 1.3.1 and a nightly Rust with
`rust-src`. It does not install cargo-afl or cargo-ziggy, so the ziggy fixture
test is skipped there.

## Project layout

```
analyzer/      C++ analyzer (src/), its .ll tests (test/) and Makefile
driver/        Python driver (reachability/) and the test suite (tests/)
fixtures/      small C, C++, Rust and mixed projects with expected results
examples/      cpp_cmake, a CMake project with virtual dispatch
scripts/       setup, LLVM selection, matrix and cross-repo scripts
docs/          the documentation
```

| Driver module | Job |
|---|---|
| `cli.py` | Command-line parsing and the `run` pipeline |
| `toolchain.py` | Finding the tools and checking their LLVM versions |
| `acquire_c.py` | C and C++ builds with gllvm, artifacts, static libraries |
| `acquire_rust.py` | Rust builds and bitcode collection |
| `link.py` | Merging bitcode with `llvm-link` |
| `analyze.py` | Running the analyzer |
| `outputs.py` | Checking, staging and publishing output files |
| `report.py` | The summary line and the advisories |
| `diagnostics.py` | Explaining a failed bitcode extraction |

| Analyzer source | Job |
|---|---|
| `main.cpp` | Options, roots and the overall flow |
| `Module.cpp` | Loading the module and register promotion |
| `CallGraph.cpp` | Direct and escape edges, value flow, externally owned memory |
| `TypeBasedResolver.cpp` | Indirect-call resolution |
| `Reachability.cpp` | The walk from the roots |
| `SlotEvidence.cpp` | Struct-field and vtable-slot evidence for confidence |
| `Metrics.cpp` | Function metrics |
| `SymbolKey.cpp`, `Demangle.cpp` | Object-file names, `key` and demangling |
| `JsonReport.cpp`, `CovLists.cpp`, `DotExport.cpp` | The outputs |
| `Toolchain.cpp` | The LLVM version the analyzer was built with |
