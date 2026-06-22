# LLVM version support, compatibility matrix, and the SVF fallback plan

## Version policy

**LLVM 21 is the minimum. Newer majors (22, 23, …) are supported for the core
analyzer.** The rules enforced by `reachability check-toolchain`
(`driver/reachability/toolchain.py`):

1. The analyzer is built against some LLVM major **M**, and **M ≥ 21**.
2. `clang`, `clang++`, `llvm-link`, and `opt` all share that same major **M**
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

The default build LLVM is chosen by `scripts/select_llvm.sh`: `llvm-config-21`
if installed, otherwise the newest installed `llvm-config-N` (N > 21). For Rust
targets make sure the chosen LLVM's **full** version is no older than rustc's
bundled LLVM, or it cannot read rustc's bitcode; override it explicitly at build
time:

```bash
make build LLVM_MAJOR=23      # analyzer on LLVM 23 (uses llvm-config-23, clang-23, …)
```

## Compatibility matrix

`make matrix` (a.k.a. `scripts/test_matrix.sh`) builds and tests the analyzer
against **every** installed `llvm-config-NN` with `NN ≥ 21`, for both the core
(type-based) backend and SVF, and **fails if any core build/test fails** — this
is the early-warning system for breakage on future LLVM releases.

Current results (2026-06-19, this machine):

| LLVM | core (type-based) | SVF (`--backend=svf`) |
|------|-------------------|-----------------------|
| 21.0.0 | ✅ PASS | ✅ PASS (analyzer goldens) |
| 22.0.0 | ✅ PASS | ❌ SVF source does not build (see below) |
| 23.0.0 | ✅ PASS | ❌ SVF source does not build (see below) |

**The core analyzer is fully functional on 21, 22, and 23.** Only the optional
SVF backend is version-limited.

**Caveat on this machine:** the distro now ships LLVM **21.0.0**, which is an
*older patch release* than rustc's bundled **21.1.1**, so LLVM-21 tools cannot
read rustc's bitcode. The matrix PASS/FAIL above is the analyzer's own `.ll`
goldens (no rustc), so it is unaffected. But end-to-end **Rust** runs need an LLVM
whose full version ≥ 21.1.1, and the auto-selected default
(`scripts/select_llvm.sh`) prefers LLVM **21** whenever `llvm-config-21` is
installed — so the default `make build` yields a 21.0.0 analyzer that **cannot**
read rustc's bitcode here. Build an LLVM-22 analyzer explicitly and point the
driver at it:

```bash
make -C analyzer LLVM_CONFIG=llvm-config-22 BUILD=build/22
export REACHABILITY_ANALYZER="$PWD/analyzer/build/22/reachability-analyzer"
```

(`make build LLVM_MAJOR=22` also works but overwrites the default `analyzer/build`;
the `build/22` subdir is the same per-version layout `make matrix` uses, is
git-ignored, and `make clean` removes it.) The SVF backend — which only builds on
LLVM 21 — therefore cannot process Rust bitcode here; the `test_svf_rust_dyn_sound`
end-to-end test **skips** with a clear reason in this configuration (C/C++ SVF
tests still run).

## SVF compatibility

SVF (pinned commit `795fd5c`, master) targets **LLVM 21.1.x**. It does not
compile against LLVM 22 or 23 due to LLVM debug-info API removals:

- **LLVM 22:** `svf-llvm/lib/LLVMUtil.cpp` uses `llvm::findDbgDeclares`, removed
  in 22 (debug-records migration; replacement `findDVRDeclares`).
- **LLVM 23:** `svf-llvm/include/SVF-LLVM/BasicTypes.h` uses
  `llvm::DITypeRefArray`, removed in 23 (replacement `DITypeArray`).

These are upstream-LLVM API changes that SVF upstream must absorb.

## Fallback plan — what to do when SVF doesn't support an LLVM version

The project is designed so SVF is **never on the critical path**. The
`IndirectResolver` interface and the type-based backend are completely
independent of SVF.

**Immediate behavior (no action needed):**
- The analyzer builds and runs normally; `--backend=type-based` (the default) is
  a sound over-approximation on every LLVM version.
- If built without SVF, `--backend=svf` exits with a clear error
  (`SVF backend not available …`, exit 2) — never a silent degradation or wrong
  result.

**To regain SVF on a newer LLVM, in order of preference:**

1. **Upgrade SVF (preferred).** When SVF upstream releases a commit that supports
   the target LLVM major, bump `SVF_COMMIT` in `scripts/build_svf.sh`, then
   `make build-svf LLVM_MAJOR=<n>`. Re-run `make matrix` to confirm. This is the
   normal maintenance path and requires no local patching.

2. **Pin the whole toolchain to an SVF-supported LLVM.** If you need SVF *now*,
   build on LLVM 21: `make build-svf` already pins LLVM 21 by default (the only
   version SVF builds against), independent of the auto-selected core default.
   Coherence holds when clang/llvm-link/opt/analyzer are all 21 and rustc's LLVM
   is ≤ 21; if the distro LLVM 21 is an older patch release than rustc (the case
   on this machine: 21.0.0 < 21.1.1), SVF can read C/C++ bitcode but not rustc's,
   so SVF is usable for C/C++ runs only. Use newer LLVM for the type-based
   backend otherwise.

3. **Local patch (last resort, brittle).** The two known breakages are simple
   renames (`findDbgDeclares`→`findDVRDeclares`, `DITypeRefArray`→`DITypeArray`),
   but a full port may surface more. Maintain a patch under `third_party/` applied
   by `build_svf.sh` only if 1 and 2 are infeasible. Treat as temporary until
   upstream catches up.

**Detection:** `make matrix` reports SVF status per version every run, so a newer
LLVM that breaks (or a new SVF commit that fixes) SVF is caught immediately rather
than discovered in production.
