# Worked examples

Four complete, start-to-finish runs on real targets:

1. [**libxml2 + a libFuzzer harness**](#1-libxml2--a-libfuzzer-harness-cc) — the
   C/C++ path, with static-library expansion and an indirect error callback.
2. [**The `url` crate + a ziggy harness**](#2-the-url-crate--a-ziggy-harness-rust)
   — the Rust path, rooted at the Rust `main`.
3. [**cpp_demangle + a cargo-afl harness**](#3-cpp_demangle--a-cargo-afl-harness-rust)
   — the Rust manual route on a parse-only harness, where the unused render half
   is generic and never emitted at all.
4. [**rustyknife + a cargo-afl harness**](#4-rustyknife--a-cargo-afl-harness-rust)
   — the Rust manual route, for a feature-gated AFL bin.

The exact counts below come from real runs (LLVM 22, type-based backend); yours
will vary with library version, build flags, and LLVM release.

---

## 1. libxml2 + a libFuzzer harness (C/C++)

Take [libxml2-2.9.2](https://raw.githubusercontent.com/vanhauser-thc/fuzzing-targets/refs/heads/master/libxml2-2.9.2.tar.gz)
and a small C++ libFuzzer harness, and compute which libxml2 functions the
harness can reach. This exercises three features at once:

- **C/C++ acquisition** — building with the gllvm wrappers to get bitcode.
- **Static-library expansion** — analyzing *all* of `libxml2.a`, not just the
  parts the harness pulls in (`--static-libs`).
- **Indirect-call resolution** — the harness installs an error callback, and the
  analyzer reaches it through a function pointer.

The harness (`harness.cc`):

```cpp
#include "libxml/parser.h"

void ignore(void *ctx, const char *msg, ...) {
  // Error handler to silence libxml2 parser messages.
}

extern "C" int LLVMFuzzerTestOneInput(const unsigned char *data, size_t size) {
    xmlSetGenericErrorFunc(NULL, &ignore);
    auto doc = xmlReadMemory(reinterpret_cast<const char *>(data), size,
                             "noname.xml", NULL, 0);
    if (doc) {
        xmlFreeDoc(doc);
        xmlCleanupParser();
    }
    return 0;
}
```

### Prerequisites

A built analyzer and `gllvm` on `PATH` — see the README
[Install](../README.md#install) section. In short:

```bash
export REACHABILITY_ANALYZER=$PWD/analyzer/build/reachability-analyzer
export PATH="$(go env GOPATH)/bin:$PATH"     # gclang / gclang++ / get-bc
source .venv/bin/activate
reachability check-toolchain                 # confirm the LLVM toolchain is coherent
```

### Step 1 — Get the sources

```bash
curl -fsSL -O https://raw.githubusercontent.com/vanhauser-thc/fuzzing-targets/refs/heads/master/libxml2-2.9.2.tar.gz
tar xf libxml2-2.9.2.tar.gz
cd libxml2-2.9.2
```

### Step 2 — Add the harness

Save the harness above as `harness.cc` in the `libxml2-2.9.2` directory. The
public headers live under `include/`, so it is compiled with `-I include`.

### Step 3 — Run the analysis

One command builds the library, compiles the harness, and analyzes the result:

```bash
reachability run --lang cpp \
  --project . \
  --build-cmd './configure --without-python --disable-shared && make -j"$(nproc)" && gclang++ -I include -c harness.cc -o harness.o' \
  --artifact harness.o \
  --static-libs all \
  --entry LLVMFuzzerTestOneInput \
  -v
```
Note the `--static-libs all` - this is important!

What each piece does:

- **`--build-cmd`** runs under the gllvm wrappers (the driver injects
  `CC=gclang` / `CXX=gclang++`), so `./configure && make` builds `libxml2.a`
  with embedded bitcode, and `gclang++ -c harness.cc` compiles the harness to a
  bitcode-carrying object. `--without-python --disable-shared` keeps the build
  small and produces a static `.libs/libxml2.a`.
- **`--artifact harness.o`** picks the harness object as the entry-bearing input.
  (Auto-detection would otherwise prefer one of libxml2's many test executables.)
- **`--static-libs all`** merges the *full* contents of every bitcode archive in
  the tree with the harness object. This is what lets the report cover all of
  libxml2, including functions the harness never calls.
- **`--entry LLVMFuzzerTestOneInput`** roots reachability at the harness. (For
  `--lang cpp` this is already a default entry; passing it is just explicit.)

> **Why `all` and not `auto`?** `auto` discovers linked archives from a linked
> binary's metadata, which a lone object file does not have. Since the harness is
> an object, `all` is the simple choice. libxml2 builds two bitcode archives —
> `libxml2.a` and a small `testdso.a` test helper — and `all` pulls in both; they
> link without conflict. If you instead link a real fuzzer binary (harness +
> `libxml2.a` + libFuzzer), point `--artifact` at that binary and use the default
> `--static-libs auto`, exactly like the README
> [static-library example](../README.md#a-target-that-links-a-static-library).

### Step 4 — Read the report

The run prints a one-line summary. On this machine it is:

```
reachable 1257 / defined 2467  (324 indirect-only, 1210 unreachable)  [backend=type-based]
```

- **defined (2467)** — every libxml2 function (plus the harness) in the merged
  module. Because of `--static-libs all`, this is the whole library, not just the
  parts the harness pulls in.
- **reachable (1257)** — the subset reachable from `LLVMFuzzerTestOneInput`.
- **unreachable (1210)** — `defined − reachable`, everything the harness can
  never touch (listed in `not_reached.txt`).
- **indirect-only (324)** — reachable functions reached *only* through an
  indirect call. This is the over-approximation surface.

**Reachable** — the parser and everything it pulls in, since `xmlReadMemory`
drives a full parse: `LLVMFuzzerTestOneInput`, `xmlReadMemory`,
`xmlParseDocument`, the lexer and SAX callbacks, `xmlFreeDoc`, `xmlCleanupParser`.

The harness's `ignore` handler is reachable too — **via an indirect edge**. The
harness takes its address (`&ignore`) and hands it to `xmlSetGenericErrorFunc`;
libxml2 later calls the installed handler through a function pointer. The
type-based backend sees an address-taken function whose type matches that call
site and adds the edge. Because `ignore` is a C++ function, it appears under its
mangled name:

```json
{
  "mangled": "_Z6ignorePvPKcz",
  "demangled": "ignore(void*, char const*, ...)",
  "via": "indirect",
  "indirect_only": true
}
```

A directly-reached function carries its source location:

```json
{
  "mangled": "xmlReadMemory",
  "demangled": "xmlReadMemory",
  "file": "parser.c",
  "line": 15376,
  "via": "direct",
  "indirect_only": false
}
```

(`via` is `direct`, `indirect`, or `both`.)

**Unreachable** (`not_reached.txt`) — the large parts of libxml2 the harness
never enters: the XPath evaluator (`xmlXPathEval`, `xmlXPathCompile`),
schema/RelaxNG validation (`xmlSchemaValidateDoc`, `xmlRelaxNGValidateDoc`), and
the serialization/writer API (`xmlSaveFile`, `xmlTextWriterStartDocument`).
Before `--static-libs all`, these would have been *absent* from the analysis
entirely; now they are correctly reported as defined-but-unreachable.

> **Over-approximation in action.** Some helpers *look* reachable through the
> type-based backend even when their feature is not. For example `xmlXPathEval`
> is unreachable, yet the XPath axis callbacks (`xmlXPathNextAncestor`, …) are
> reported reachable `via indirect`: they are address-taken and their type
> matches an indirect call site the parser reaches. This is the sound-leaning
> bias at work — never miss a real edge, even at the cost of some false ones.

### Step 5 — Instrument only reachable code

Feed either list to clang so SanitizerCoverage instruments just the code the
harness can reach — smaller binaries and faster fuzzing:

```bash
# instrument ONLY reachable functions:
clang -fsanitize-coverage=trace-pc-guard -fsanitize-coverage-allowlist=reached.txt ...
# OR: instrument everything EXCEPT unreachable functions:
clang -fsanitize-coverage=trace-pc-guard -fsanitize-coverage-ignorelist=not_reached.txt ...
```

### Troubleshooting

- **`gclang: not found` during the build.** Put gllvm on `PATH`
  (`export PATH="$(go env GOPATH)/bin:$PATH"`).
- **`--static-libs all` and overlapping archives.** Archive manifests identify
  the exact bitcode objects each archive contains, so an archive wholly covered
  by an already selected archive is skipped without relying on object basenames.
  If selected archives still contain conflicting definitions, the merge fails
  rather than silently replacing one function body. Use `auto` or narrow the
  archive set when those definitions are genuinely duplicated.
- **`configure` fails on a very new system.** libxml2-2.9.2 predates current
  toolchains; the analyzer never treats warnings as errors, but if configure
  itself errors, add the flags the project needs (e.g. `--without-lzma`) to the
  `--build-cmd`.

---

## 2. The `url` crate + a ziggy harness (Rust)

Now the Rust path. The [ziggy](https://github.com/srlabs/ziggy) repository ships
an example harness that fuzzes the real `url` crate from several angles
(`examples/url/src/main.rs`). Its fuzz loop lives inside `fn main()`:

```rust
fn main() {
    ziggy::fuzz!(|data: &[u8]| {
        if let Ok(string) = std::str::from_utf8(data) {
            invariant_fuzz(string);      // url::Url::parse, then assert an invariant
            differential_fuzz(string);   // parse two ways, compare
            correctness_fuzz(string);
            consistency_fuzz(string);
            idempotency_fuzz(string);
        }
    });
}
```

A ziggy harness has **no `LLVMFuzzerTestOneInput`** — the entry is the Rust
`main`. `--lang ziggy` acquires the Rust bitcode and roots there automatically.

### Prerequisites

A built analyzer and a recent **nightly** rustc/cargo. The analyzer's LLVM must
be at least as new as rustc's (here: analyzer 22, rustc 21.1.1 — fine).
`reachability check-toolchain` confirms it. `--lang ziggy` builds via `cargo afl`
under the hood, so the AFL++ LLVM plugins must be installed
(`cargo afl config --build --plugins --force`).

### Step 1 — Get the harness

```bash
git clone https://github.com/srlabs/ziggy
cd ziggy
```

### Step 2 — Run the analysis

```bash
reachability run --lang ziggy --project examples/url -v
```

What happens:

- **`--lang ziggy`** builds through ziggy's own driver
  (`cargo ziggy build --no-honggfuzz`) under a `RUSTC_WRAPPER` that adds
  `--emit=llvm-bc`, collects each crate's `.bc`, merges them, and roots at `main`.
  Collection reads the bitcode straight from each rustc `--out-dir`, so it does
  not matter that `examples/url` is a workspace member whose target dir lives at
  the workspace root — no `CARGO_TARGET_DIR` juggling needed.
- **The build matches the fuzz binary automatically.** Because it is ziggy's real
  build, the bitcode already carries the same `cfg(fuzzing)`, optimization level,
  and instrumentation as the binary you instrument — so the reachable set lines
  up. `--profile release` adds `--release`; `--build-cmd` overrides the command
  for a specific target/sanitizer/profile. Clean first (`cargo clean`, or pass
  `--clean`) if the build is already cached, or no bitcode is emitted.

### Step 3 — Read the report

The summary on this machine:

```
reachable 11739 / defined 17428  (456 indirect-only, 5689 unreachable)  [backend=type-based]
```

**Rooting at the Rust `main`.** The token `main` resolves to *two* symbols, shown
in the JSON `entries`:

```json
"entries": ["main", "_ZN8url_fuzz4main17h58c435803ec45a52E"]
```

The first is the C-ABI shim that starts the Rust runtime; the second is the real
`url_fuzz::main` holding the `ziggy::fuzz!` body. Rooting at the bare token finds
both, so reachability is complete — you never type a mangled symbol. (Rooting at
only the C shim would reach almost nothing, since the real work hangs off
`url_fuzz::main`.)

**Reachable** — the harness and the `url` parser it drives: `url_fuzz::main`, all
five strategies (`invariant_fuzz`, `differential_fuzz`, `correctness_fuzz`,
`consistency_fuzz`, `idempotency_fuzz`), and `url::ParseOptions::parse` with the
parser internals beneath it.

**Unreachable** (`not_reached.txt`) — API the harness never calls. A clean
example: the harness only *parses* URLs, so the whole mutation API is
unreachable — `url::Url::set_scheme`, `set_host`, `set_port`, and the other 16
`Url::set_*` setters. Much of the Unicode/IDNA machinery (`icu_*`, `zerovec`) and
the proc-macro crates (`proc_macro2`, parts of `syn`) are unreachable too.

> **Over-approximation is heavier in Rust.** The reachable count is large
> (11,739 of 17,428) because Rust dispatches pervasively through trait-object
> vtables. The type-based backend treats every address-taken function whose
> signature matches a reached indirect call site as a candidate, so once any
> `Debug::fmt`-shaped call is reachable, same-shaped `fmt` impls across
> dependencies (much of `syn`, parts of `icu_*`) are pulled in as well. That is
> the sound-leaning bias: it never drops a real edge. The `indirect-only` count
> (456) measures functions reached *only* this way.

---

## 3. cpp_demangle + a cargo-afl harness (Rust)

[cpp_demangle](https://github.com/gimli-rs/cpp_demangle) is a pure-Rust demangler
for C++ (Itanium) symbols. It ships a cargo-afl harness at
`src/bin/afl_runner.rs` that feeds raw fuzz bytes straight into the parser:

```rust
#[macro_use]
extern crate afl;
extern crate cpp_demangle;

fn main() {
    afl::fuzz!(|bytes| {
        let _ = cpp_demangle::Symbol::new(bytes);
    });
}
```

Like ziggy, a cargo-afl harness keeps its fuzz loop in `main`, so reachability
roots at the Rust `main` (the `--lang afl` shape). `reachability run --lang afl`
builds with `cargo afl build`, but `afl_runner` is **feature-gated**
(`required-features = ["afl"]`), so the bin is skipped unless you pass the
feature: `--build-cmd 'cargo afl build --features afl --bin afl_runner'` (which
needs the AFL++ LLVM plugins installed). The **manual route** shown here needs
neither the plugins nor a working AFL runtime — emit bitcode with the feature
enabled, `llvm-link`, then run the analyzer directly. cpp_demangle pins a current
`afl` 0.17 that compiles cleanly on a modern toolchain; only the final link fails
(the AFL runtime is absent), which is exactly what `--emit=llvm-bc` expects.

### Step 1 — Get it

```bash
git clone https://github.com/gimli-rs/cpp_demangle
cd cpp_demangle
```

### Step 2 — Emit the bitcode

```bash
RUSTFLAGS="--emit=llvm-bc -Cembed-bitcode=yes -Ccodegen-units=1" \
  cargo build --features afl --bin afl_runner
```

The link fails with `undefined symbol: __afl_manual_init` (and
`__afl_persistent_loop`, `__afl_fuzz_len`) — AFL runtime symbols injected only by
`cargo afl build`. That is expected under `--emit=llvm-bc`; the per-crate `.bc` in
`target/debug/deps/` are already written.

### Step 3 — Link and analyze

```bash
llvm-link-22 target/debug/deps/*.bc -o merged.bc     # one .bc per crate; clean deps/ first if you rebuilt
reachability-analyzer merged.bc --entry main \
  --out cpp_demangle.json --reached-out reached.txt --not-reached-out not_reached.txt
```

(`reachability-analyzer` is the built binary or `$REACHABILITY_ANALYZER`;
`llvm-link-22` is the LLVM tool matching the analyzer's major. Run directly it
writes the JSON report and the two lists; `reachability run` wraps these steps
and additionally prints a one-line summary.)

### Step 4 — Read the report

The report's `summary` (in `cpp_demangle.json`) holds the counts:

```json
"backend": "type-based",
"summary": { "defined": 2446, "reachable": 1134, "indirect_only": 26, "unreachable": 1312 }
```

As with ziggy, `--entry main` resolves to both the C-ABI shim and the real Rust
main — `entries: ["main", "_ZN10afl_runner4main17h…E"]`.

**Reachable** — within cpp_demangle, the parser and little past it:
`afl_runner::main` → the `afl::fuzz!` closure → `cpp_demangle::Symbol::new`, then
the recursive-descent grammar beneath it — the `ast::*::parse_internal` rules,
`OperatorName` / `CtorDtorName`, the `starts_with` lookahead predicates,
`ParseContext`, and the `subs::SubstitutionTable` *inserts* that record
back-reference candidates as the parse runs. (Most of the 1,134 total are the
Rust runtime — `core` / `alloc` / `std` — that the parser pulls in.)

```json
{
  "mangled": "_ZN12cpp_demangle15Symbol$LT$T$GT$3new17h66f1211aad4bb946E",
  "demangled": "cpp_demangle::Symbol$LT$T$GT$::new::h66f1211aad4bb946",
  "file": "src/lib.rs",
  "line": 179,
  "via": "direct",
  "indirect_only": false
}
```

**Unreachable** (`not_reached.txt`) — the *render* side of the library, because
the harness parses but never demangles. `Symbol::new` builds the AST; it never
calls `Symbol::demangle`, so the substitution-*resolution* helpers
(`SubstitutionTable::get_type`, `pop`, `non_substitution`), the back-reference
resolvers (`TypeHandle::back_reference`, `TemplateParam::resolve`, …) and the AST
accessors used only while printing (`BareFunctionType::args` / `ret`) are
defined-but-unreachable — as is the whole options-builder API
(`DemangleOptions::new`, `no_params`, `recursion_limit`, …) the harness never
constructs.

> **Dead *generic* code is absent, not merely unreachable.** The demangler proper
> — `Symbol::demangle`, the `Demangle` / `DemangleWrite` impls, the `Display`
> rendering — appears in *neither* list. It is generic, and nothing in this build
> instantiates it, so rustc never monomorphizes it and emits no bitcode for it.
> Reachability can only classify functions that exist; an uninstantiated generic
> is invisible to it. (cpp_demangle's *other* harness — the cargo-fuzz
> `fuzz/fuzzers/parse_and_stringify.rs` — does call `sym.demangle()`; point a run
> at that one and the render half of the library snaps into `defined`, much of it
> then reachable.)

> **The defined set includes the build toolchain.** `afl` 0.17 pulls in `semver`,
> `xdg`, `home`, and `rustc_version` (its build-script and runtime-config crates);
> their bitcode is emitted and merged, padding `defined` (2,446), and a few even
> read as reachable through type-based over-approximation. The 26 `indirect-only`
> functions are almost all `catch_unwind` / `FnOnce::call_once` vtable shims from
> the `afl::fuzz!` panic-handling machinery — the harness's dispatch surface, not
> the library's.

---

## 4. rustyknife + a cargo-afl harness (Rust)

[rustyknife](https://github.com/zerospam/rustyknife) is an email-parsing library
with a cargo-afl harness at `src/bin/fuzz_mailbox.rs`. Like ziggy, a cargo-afl
harness puts the fuzz loop in `main`, so reachability roots at the Rust `main`
(the `--lang afl` shape):

```rust
#[macro_use]
extern crate afl;

fn main() {
    fuzz!(|data: &[u8]| {
        let _ = rustyknife::rfc5321::mailbox::<rustyknife::behaviour::Intl>(data);
    });
}
```

Two things differ from the `url` walkthrough, so the native `--lang afl` path
(which would run `cargo afl build`) does not fit, and this one uses the **manual
route** — emit bitcode, `llvm-link`, then run the analyzer directly:

- The `fuzz_mailbox` bin is **feature-gated** (`required-features = ["fuzz"]`),
  so it builds only with `--features fuzz`.
- rustyknife pins the **old `afl` 0.8** crate, whose bundled AFL does not compile
  on current toolchains — so `cargo afl build` cannot complete. We need only the
  bitcode, not AFL's runtime, so we nudge its build past two checks.

### Step 1 — Get it

```bash
git clone https://github.com/zerospam/rustyknife
cd rustyknife
```

### Step 2 — Emit the bitcode

`afl` 0.8 builds a 2020-era AFL that trips a modern compiler's x86 self-test and
its `-Werror`. Skip the first with `AFL_NO_X86=1`, and defeat the second with
tiny compiler wrappers that append `-Wno-error` (any method works — the C runtime
is irrelevant to reachability):

```bash
mkdir -p /tmp/nowerror
for t in cc clang clang++; do
  printf '#!/bin/sh\nexec /usr/bin/%s "$@" -Wno-error\n' "$t" > /tmp/nowerror/$t
  chmod +x /tmp/nowerror/$t
done
export PATH="/tmp/nowerror:$PATH" CC=/tmp/nowerror/cc AFL_NO_X86=1

RUSTFLAGS="--emit=llvm-bc -Cembed-bitcode=yes -Ccodegen-units=1" \
  cargo build --features fuzz --bin fuzz_mailbox
```

The final link fails (`undefined reference to __afl_manual_init` — AFL runtime
symbols added only by `cargo afl build`); that is expected under `--emit=llvm-bc`.
Only the per-crate `.bc` in `target/debug/deps/` matter, and they are now there.

Match the profile and `-Ccodegen-units` here to the build that produces the
instrumented binary (add `--release` and the matching codegen-units for a release
fuzz build); they decide which monomorphizations are emitted. With
`-Ccodegen-units` above 1, rustc splits each crate into several
`deps/<crate>-<hash>.<cgu>.rcgu.bc` — pass all of them to `llvm-link`.

### Step 3 — Link and analyze

```bash
llvm-link-22 target/debug/deps/*.bc -o merged.bc     # one .bc per crate; clean deps/ first if you rebuilt
reachability-analyzer merged.bc --entry main \
  --out rustyknife.json --reached-out reached.txt --not-reached-out not_reached.txt
```

(`reachability-analyzer` is the built binary or `$REACHABILITY_ANALYZER`;
`llvm-link-22` is the LLVM tool matching the analyzer's major. Run directly it
writes the JSON report and the two lists; `reachability run` wraps these steps
and additionally prints a one-line summary.)

### Step 4 — Read the report

The report's `summary` (in `rustyknife.json`) holds the counts:

```json
"backend": "type-based",
"summary": { "defined": 17000, "reachable": 2217, "indirect_only": 79, "unreachable": 14783 }
```

As with ziggy, `--entry main` resolves to both the C-ABI shim and the real Rust
main — `entries: ["main", "_ZN12fuzz_mailbox4main17h…E"]`.

**Reachable** — a tight slice: `fuzz_mailbox::main` → `afl::fuzz` → the closure →
`rustyknife::rfc5321::mailbox`, and the `nom` parser combinators beneath it
(~150 `nom` functions, plus rfc5321 grammar rules such as `dot_string`).

**Unreachable** — rustyknife's *other* parsers, which this harness never calls:
the RFC 5322 message grammar (`rustyknife::rfc5322::*`) and the RFC 2047
encoded-word decoder (`rustyknife::rfc2047::decode_text`). The harness targets
only the RFC 5321 mailbox grammar, and the report reflects exactly that.

> **A focused harness yields a focused report.** Only 2,217 of 17,000 functions
> are reachable, just 79 of them indirect-only. Because the harness funnels
> through a single parser entry, the over-approximation stays small — a sharp
> contrast with the `url` example (67% reachable), where pervasive trait-object
> dispatch pulled in far more.
