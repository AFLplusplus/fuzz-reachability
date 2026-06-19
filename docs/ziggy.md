# Reachability for a ziggy fuzz harness

A [ziggy](https://github.com/srlabs/ziggy) harness is an ordinary Rust **binary
crate**: the fuzz loop lives in `ziggy::fuzz!(|data: &[u8]| { … })` *inside*
`fn main()`. There is no `LLVMFuzzerTestOneInput`. The entry point is therefore
the **Rust `main`**, and the rest of the merged-bitcode pipeline is identical to
the normal Rust flow ([README](../README.md) §3).

Only two things are ziggy-specific:

1. You emit bitcode for a **bin** crate (the harness) and all its dependencies.
2. You root the analysis at the **mangled Rust `main`** — *not* the bare `main`
   symbol (the C-ABI shim, which dead-ends in precompiled `std`; see the gotcha
   below).

## TL;DR (via the driver)

The driver knows the ziggy shape — `--lang ziggy` acquires the Rust bitcode and
roots at `main` automatically:

```bash
reachability run --lang ziggy --project <harness> --out reach.json
```

(Caveats for large/aptos-style projects — custom `rustflags`, stale `.bc` — are
below under "Using the driver".)

## TL;DR (manual)

Here `reachability-analyzer` is the built binary
(`analyzer/build/reachability-analyzer`, or `$REACHABILITY_ANALYZER`), and
`llvm-link-22` is the LLVM tool matching the analyzer's major (≥ rustc's LLVM —
see step 2). `--entry main` is resolved flexibly to the Rust `main`; no mangled
symbol is needed.

```bash
cd <harness>                      # the ziggy bin crate directory

# 1. Emit per-crate bitcode (the final link may fail; only the .bc matter).
#    Add any rustflags the project's .cargo/config.toml requires (see below).
RUSTFLAGS="--emit=llvm-bc -Cembed-bitcode=yes -Ccodegen-units=1" cargo build

# 2. Merge with an llvm-link whose LLVM major matches the analyzer (>= rustc's).
llvm-link-22 target/debug/deps/*.bc -o merged.bc

# 3. Analyze, rooted at the Rust main (resolved from the bare token `main`).
reachability-analyzer merged.bc --entry main \
  --out reach.json --reached-out reached.txt --not-reached-out not_reached.txt
```

## Step by step

### 1. Emit bitcode

Same `RUSTFLAGS` as any Rust target. Two project-specific wrinkles:

- **Custom rustflags.** A setting of `RUSTFLAGS` in the environment *replaces*
  (does not merge with) a `rustflags` array in the project's
  `.cargo/config.toml`. If the project needs flags to build (e.g. aptos sets
  `rustflags = ["--cfg", "tokio_unstable"]`), include them yourself:

  ```bash
  RUSTFLAGS="--cfg tokio_unstable --emit=llvm-bc -Cembed-bitcode=yes -Ccodegen-units=1" cargo build
  ```

- **Where the `.bc` land.** They go to `<target>/debug/deps/*.bc`. For a crate
  that is its own package or is `exclude`d from a workspace, that is
  `<harness>/target/debug/deps`. For a **workspace member**, cargo writes to the
  *workspace* target (`<workspace>/target/debug/deps`); point cargo at a local
  dir with `CARGO_TARGET_DIR=$PWD/target` if you want it next to the harness.

If you rebuild with different flags, cargo keeps the **old** per-crate `.bc`
(one extra hash per build). Linking duplicates fails (`llvm-link`: redefined
symbol), so start from a clean `deps/` (or keep only the newest `.bc` per crate)
before merging.

### 2. Merge

Use an `llvm-link` whose LLVM major matches the analyzer and is **≥ rustc's**
bundled LLVM (the analyzer reads the merged module; see
[`llvm-support.md`](llvm-support.md)). On a box where the default toolchain is
LLVM 22, use `llvm-link-22`.

### 3. Root at `main` — the C `main` shim is handled for you

Pass `--entry main`. The analyzer resolves it flexibly (exact symbol, demangled
name, or `::main` suffix), so you never type a mangled symbol. A Rust bin has
*two* `main`-ish symbols, and `main` matches both:

| symbol | what it is |
|--------|------------|
| `main` | C-ABI shim that calls `std::rt::lang_start` (in precompiled `std`, a declaration here — dead-ends on its own). |
| `_ZN…<crate>…main…E` / `_RNvC…<crate>…main` | the real Rust `main` — your harness body, incl. the `ziggy::fuzz!` closure. |

Because the token `main` matches the **demangled** `<crate>::main` too, rooting at
`main` includes the real Rust `main` (and the harmless shim), so reachability is
complete. If you prefer to be explicit, pass the demangled name, e.g.
`--entry global_storage::main`. To see the exact symbol:

```bash
llvm-nm-22 --defined-only target/debug/deps/<bin>-*.bc | grep ' T ' | grep main
reachability-analyzer --selftest-demangle '<symbol>'   # confirm it is <crate>::main
```

The crate name is the **bin target's** name with `-` → `_` (e.g. a `[[bin]]
name = "global-storage"` → `global_storage`); for a default-named bin it is the
package name.

### 4. Analyze

```bash
reachability-analyzer merged.bc --entry main \
  --out reach.json --reached-out reached.txt --not-reached-out not_reached.txt
```

Output is the usual triple (JSON report + sancov allow/ignore lists; see
[README §Output](../README.md#output)). The `ziggy::fuzz!` closure
(`<crate>::main::{{closure}}`) is reached from `main` and pulls in the whole
per-input code path.

## Using the driver

`reachability run --lang ziggy --project <harness> --out reach.json` does steps
1–4 in one shot (`--lang ziggy` acquires the Rust bitcode and defaults `--entry`
to `main`). Caveats for large/aptos-style projects:

- It sets its own `RUSTFLAGS` (no project `--cfg` flags) and globs **all**
  `<project>/target/debug/deps/*.bc`, so it needs a project that builds under
  those flags and a clean `deps/` (one `.bc` per crate). When either does not
  hold, use the manual steps above.
- It globs `<project>/target/...`; for a workspace **member** export
  `CARGO_TARGET_DIR=<project>/target` first so the bitcode lands where the glob
  looks.

## Worked example: move-smith `global-storage`

`~/aptos/move-smith/fuzz-ziggy` is a ziggy harness (`[[bin]] name =
"global-storage"`, `exclude`d from the aptos workspace, so it has its own
`target/`). aptos needs `--cfg tokio_unstable`.

```bash
cd ~/aptos/move-smith/fuzz-ziggy
RUSTFLAGS="--cfg tokio_unstable --emit=llvm-bc -Cembed-bitcode=yes -Ccodegen-units=1" cargo build
llvm-link-22 target/debug/deps/*.bc -o /tmp/merged.bc        # keep one .bc per crate
reachability-analyzer /tmp/merged.bc --entry main \
  --out /tmp/reach.json --reached-out /tmp/reached.txt --not-reached-out /tmp/not_reached.txt
```

`--entry main` resolves to the demangled `global_storage::main` (the Rust main,
`_ZN14global_storage4main17hcc7e51cc3974f743E`) plus the C-ABI shim.

Result (type-based backend): **229,419 reachable / 296,199 defined** rooted at
`main` (227,492 if you root only at `global_storage::main`). The reachable set
includes the `ziggy::fuzz!` closure and the full execution stack it drives —
`msmith::…::execute_without_save`, `TransactionalInputBuilder`,
`TransactionalExecutor`, `TransactionalResult::is_bug`, etc. Rooting at the bare
`main` shim instead yields only 2 — the lang_start gotcha above.
