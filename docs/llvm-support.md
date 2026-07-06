# LLVM version support and compatibility matrix

## Version policy

**LLVM 21 is the minimum. Newer majors (22, 23, …) are supported for the core
analyzer.** The rules enforced by `reachability check-toolchain`
(`driver/reachability/toolchain.py`):

1. The analyzer is built against some LLVM major **M**, and **M ≥ 21**.
2. `clang`, `clang++`, and `llvm-link` all share that same major **M**
   (one coherent toolchain produces and merges the bitcode the analyzer reads).
3. **rustc's** bundled LLVM must be old enough for the tools to read its bitcode.
   LLVM auto-upgrades *older* bitcode but cannot read *newer* bitcode, so the
   analyzer/tools must be at least as new as every bitcode producer. The major
   check (`rustc_major ≤ M`) is a coarse gate; the Rust path additionally
   requires the tools' **full** version to be ≥ rustc's full LLVM version
   (enforced by `rust_bitcode_readable` / `assert_rust_bitcode_readable`). A
   same-major distro LLVM that is an *older patch release* than rustc's cannot
   read rustc's bitcode (`llvm-link: Invalid record`) — this is the common gotcha
   when a distro ships, say, LLVM 21.0.0 while rustc bundles 21.1.1.

The default build LLVM is chosen by `scripts/select_llvm.sh`: the newest
installed `llvm-config-N` with `N ≥ 21`. Newer tools read older bitcode but not
newer, so the newest toolchain is the safest default. For Rust targets make sure
the chosen LLVM's **full** version is no older than rustc's bundled LLVM, or it
cannot read rustc's bitcode; override it explicitly at build time:

```bash
make build LLVM_MAJOR=23      # analyzer on LLVM 23 (uses llvm-config-23, clang-23, …)
```

## Compatibility matrix

`make matrix` (a.k.a. `scripts/test_matrix.sh`) builds and tests the analyzer
against **every** installed `llvm-config-NN` with `NN ≥ 21`, and **fails if any
build/test fails** — this is the early-warning system for breakage on future
LLVM releases.

Current results (2026-06-19, this machine):

| LLVM | analyzer (type-based) |
|------|-----------------------|
| 21.0.0 | ✅ PASS |
| 22.0.0 | ✅ PASS |
| 23.0.0 | ✅ PASS |

**The analyzer is fully functional on 21, 22, and 23.**

**Caveat — when only LLVM 21.0.0 is installed:** the distro ships LLVM **21.0.0**,
an *older patch release* than rustc's bundled **21.1.1**, so LLVM-21 tools cannot
read rustc's bitcode. The matrix PASS/FAIL above is the analyzer's own `.ll`
goldens (no rustc), so it is unaffected. But end-to-end **Rust** runs need an LLVM
whose full version ≥ rustc's. The auto-selected default
(`scripts/select_llvm.sh`) already prefers the **newest** installed major, so it
sidesteps this whenever a newer LLVM (22, 23, …) is installed. If 21.0.0 is the
only LLVM present, install a newer one (e.g. via https://apt.llvm.org/llvm.sh),
or build against it explicitly and point the driver at it:

```bash
make -C analyzer LLVM_CONFIG=llvm-config-22 BUILD=build/22
export REACHABILITY_ANALYZER="$PWD/analyzer/build/22/reachability-analyzer"
```

(`make build LLVM_MAJOR=22` also works but overwrites the default `analyzer/build`;
the `build/22` subdir is the same per-version layout `make matrix` uses, is
git-ignored, and `make clean` removes it.)
