# SVF backend — build spike notes (Milestone 5)

## Outcome: SUCCESS

SVF builds cleanly against the **system LLVM 21.1.8** and works as a real
`--backend=svf`, passing the same soundness invariant as the type-based backend
on every fixture.

## Why it worked

The top risk in the spec (§8) was SVF lagging LLVM releases. As of the pinned
commit, **SVF master already targets LLVM 21.1.x** (`setup.sh` references
`llvm-21.1.0.obj`), which matches our system LLVM 21.1.8 at the major+minor
level. No source patches were needed.

## Build recipe (`scripts/build_svf.sh`)

- **SVF commit:** `795fd5cbbc2e5e343277c391300ba1d1d9903a73` (master, 2026-06-09).
- **LLVM:** system `/usr/lib/llvm-21` (NOT SVF's bundled download) — preserves the
  single-shared-LLVM prerequisite, since clang/rustc/analyzer all use 21.1.8.
- **Z3:** prebuilt `z3-4.8.8-x64-ubuntu-16.04` downloaded to `third_party/z3.obj`
  (the version SVF's own `build.sh` uses). No system Z3 required.
- Outputs `libSvfCore.a`, `libSvfLLVM.a`, `extapi.bc`, and a CMake config
  (`SVF::SvfCore`, `SVF::SvfLLVM`) under `third_party/SVF/install`.

> **Build system note:** the analyzer now builds with a plain Makefile (no
> CMake). Enable SVF with `make build-svf LLVM_MAJOR=21` (or
> `make -C analyzer SVF=1 BUILD=build-svf`). The Makefile links the SVF static
> archives + the LLVM dylib + z3 directly. SVF's *own* vendored build
> (`scripts/build_svf.sh`) still uses SVF's CMake internally — that is an
> external dependency, not part of our build. See also
> [`llvm-support.md`](llvm-support.md) for the current per-version status.

## Analyzer integration (`analyzer/src/SVFResolver.cpp`)

- Enabled via `REACHABILITY_ENABLE_SVF` (set by `make SVF=1`), which links the
  SVF static archives, the LLVM dylib, and z3.
- `SVFResolver` builds SVF over our **in-memory** module
  (`LLVMModuleSet::buildSVFModule(Module&)`) so `getCallICFGNode` maps our exact
  call instructions, runs `AndersenWaveDiff`, and reads per-callsite callees via
  `getIndCSCallees`. Callees map back to `llvm::Function*` by name.
- **Soundness:** for any indirect call SVF does not resolve, `SVFResolver` falls
  back to the type-based resolver, so `--backend=svf` is never less sound than
  the default.

## Gotchas handled

1. **zstd export quirk** — LLVM 21's `LLVMSupport` link interface references a
   `zstd::libzstd_shared` target that Ubuntu's libzstd ships no config for; the
   analyzer's CMake defines it from `libzstd.so.1`.
2. **`z3::libz3` target** — created by SVF's bundled `FindZ3` (point it at
   `third_party/z3.obj` via `Z3_DIR`); do NOT also define it manually or CMake
   errors on the duplicate.
3. **`LLVM` target** — SVF's exported targets reference a target literally named
   `LLVM`; the analyzer defines it as an imported dylib (`libLLVM.so`). When SVF
   is on we link the LLVM dylib instead of the component static libs to keep a
   single LLVM in the process.
4. **`extapi.bc` at runtime** — set via `ExtAPI::setExtBcPath()` from the
   compile-time `REACHABILITY_SVF_EXTAPI` path, so no `SVF_DIR` env is required.
5. **stdout pollution** — SVF dumps statistics to stdout by default; disabled via
   `Options::PStat=false` so the analyzer's JSON stays clean on stdout.

## If SVF ever stops building against the pinned LLVM

The `IndirectResolver` interface and the type-based backend are fully
independent of SVF. Build without SVF (`make build`, i.e. `SVF=0`, the default)
and `--backend=svf` returns a clear "SVF backend not available" error (exit 2).
No silent degradation. This is exactly what happens on LLVM 22/23 today — see
[`llvm-support.md`](llvm-support.md).
