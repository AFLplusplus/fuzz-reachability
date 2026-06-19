# LLVM version support, compatibility matrix, and the SVF fallback plan

## Version policy

**LLVM 21 is the minimum. Newer majors (22, 23, …) are supported for the core
analyzer.** The rules enforced by `reachability check-toolchain`
(`driver/reachability/toolchain.py`):

1. The analyzer is built against some LLVM major **M**, and **M ≥ 21**.
2. `clang`, `clang++`, `llvm-link`, and `opt` all share that same major **M**
   (one coherent toolchain produces and merges the bitcode the analyzer reads).
3. **rustc's** bundled LLVM major must be **≤ M**. LLVM auto-upgrades *older*
   bitcode but cannot read *newer* bitcode, so the analyzer/tools must be at
   least as new as every bitcode producer. (rustc here is 21; building the
   analyzer on 21/22/23 all satisfy this.)

Pick the version at build time:

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
| 21.1.8 | ✅ PASS | ✅ PASS |
| 22.1.8 | ✅ PASS | ❌ SVF source does not build (see below) |
| 23.0.0 | ✅ PASS | ❌ SVF source does not build (see below) |

**The core analyzer is fully functional on 21, 22, and 23.** Only the optional
SVF backend is version-limited.

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
   build the entire analyzer toolchain on LLVM 21 (`make build-svf LLVM_MAJOR=21`)
   and run analyses there. Coherence is preserved because clang/llvm-link/opt/
   analyzer are all 21 and rustc is 21. Use newer LLVM only for runs where the
   type-based backend suffices.

3. **Local patch (last resort, brittle).** The two known breakages are simple
   renames (`findDbgDeclares`→`findDVRDeclares`, `DITypeRefArray`→`DITypeArray`),
   but a full port may surface more. Maintain a patch under `third_party/` applied
   by `build_svf.sh` only if 1 and 2 are infeasible. Treat as temporary until
   upstream catches up.

**Detection:** `make matrix` reports SVF status per version every run, so a newer
LLVM that breaks (or a new SVF commit that fixes) SVF is caught immediately rather
than discovered in production.
