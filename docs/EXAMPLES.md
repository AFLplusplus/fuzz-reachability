# Worked examples

Four complete runs on real targets:

1. [**libxml2 + a libFuzzer harness**](#1-libxml2--a-libfuzzer-harness-cc):
   the C/C++ path, with static-library expansion, an error callback, and a
   comparison with LTO.
2. [**The `url` crate + a ziggy harness**](#2-the-url-crate--a-ziggy-harness-rust):
   the Rust path, starting from the Rust `main`.
3. [**cpp_demangle + a cargo-afl harness**](#3-cpp_demangle--a-cargo-afl-harness-rust):
   a feature-gated harness that only parses, so the unused render half is
   never compiled.
4. [**rustyknife + a cargo-afl harness**](#4-rustyknife--a-cargo-afl-harness-rust):
   a harness whose old `afl` crate needs build workarounds.

All runs used reachability 1.2-dev (commit `f5adeab`) with the analyzer on
LLVM 23. The Rust runs used rustc 1.99.0-nightly (2026-07-12, LLVM 22.1.8) and
cargo-afl 0.18.2. Your counts will differ with other library versions, flags
and LLVM releases.

---

## 1. libxml2 + a libFuzzer harness (C/C++)

Take [libxml2-2.9.2](https://raw.githubusercontent.com/vanhauser-thc/fuzzing-targets/refs/heads/master/libxml2-2.9.2.tar.gz)
and a small C++ libFuzzer harness, and compute which libxml2 functions the
harness can reach. This shows three features:

- **C/C++ builds**: building with the gllvm wrappers to get bitcode.
- **Static-library expansion**: analyzing all of `libxml2.a`, not only the
  parts the harness pulls in (`--static-libs`).
- **Indirect calls**: the harness installs an error callback, and the
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

A built analyzer and gllvm on `PATH`, see [Install](../README.md#install):

```bash
export REACHABILITY_ANALYZER=$PWD/analyzer/build/reachability-analyzer
export PATH="$(go env GOPATH)/bin:$PATH"     # gclang, gclang++, get-bc
source .venv/bin/activate
reachability check-toolchain
```

### Step 1 — Get the sources

```bash
curl -fsSL -O https://raw.githubusercontent.com/vanhauser-thc/fuzzing-targets/refs/heads/master/libxml2-2.9.2.tar.gz
tar xf libxml2-2.9.2.tar.gz
cd libxml2-2.9.2
```

### Step 2 — Add the harness

Save the harness above as `harness.cc` in `libxml2-2.9.2/`. The public
headers are under `include/`, so it is compiled with `-I include`.

### Step 3 — Run the analysis

One command builds the library, compiles the harness, and analyzes the result:

```bash
reachability run \
  --lang cpp \
  --project . \
  --build-cmd './configure --without-python --disable-shared && make -j"$(nproc)" && gclang++ -I include -c harness.cc -o harness.o' \
  --artifact harness.o \
  --static-libs all \
  --entry LLVMFuzzerTestOneInput \
  -v
```

What each part does:

- **`--build-cmd`** runs with the gllvm wrappers as `CC` and `CXX`, so
  `./configure && make` builds `libxml2.a` with bitcode, and `gclang++ -c`
  compiles the harness to an object with bitcode. `--without-python
  --disable-shared` keeps the build small and static. An auto-detected
  `configure` build adds `--disable-shared` itself; it is spelled out here
  because the command is explicit.
- **`--artifact harness.o`** picks the harness object. Without it, the search
  would prefer one of libxml2's many test programs.
- **`--static-libs all`** merges every bitcode archive in the tree with the
  harness object. This is what makes the report cover all of libxml2,
  including functions the harness never calls.
- **`--entry LLVMFuzzerTestOneInput`** is already a default for `--lang cpp`;
  it is only spelled out here.

> **Why `all` and not `auto`?** `auto` reads the linked archives from a linked
> binary, and a lone object has no such list. libxml2 builds two bitcode
> archives, `libxml2.a` and a small `testdso.a` test helper, and `all` takes
> both; they link without conflict. If you link a real fuzzer binary (harness,
> `libxml2.a` and libFuzzer), point `--artifact` at that binary and keep the
> default `auto`; see [Static libraries](usage.md#static-libraries).

### Step 4 — Read the report

The summary line:

```
reachable 1901 / defined 3035  (329 indirect-only, 14 low-confidence, 1134 unreachable)  [backend=type-based]
```

- **defined (3035)**: every libxml2 function and the harness. With
  `--static-libs all` this is the whole library.
- **reachable (1901)**: the functions reachable from `LLVMFuzzerTestOneInput`.
- **unreachable (1134)**: everything the harness can never reach, listed in
  `not_reached.txt`.
- **indirect-only (329)**: reachable functions that only indirect calls reach.
  This is where the over-approximation lives.
- **low-confidence (14)**: indirect-only functions that only a type match
  supports. They stay reachable.

The run also prints
`note: added 40 root(s) referenced by name (dlsym/dlopen-by-name reachability): xmlSAX2InternalSubset …`.
libxml2 calls `dlsym` and contains the names of 40 of its own functions as
strings, so they become roots ([why](analysis.md#runtime-name-lookup)). With
`--no-name-roots` the run reaches 1,867 functions instead of 1,901.

**Reachable**: the parser and everything it pulls in, since `xmlReadMemory`
runs a full parse: `LLVMFuzzerTestOneInput`, `xmlReadMemory`,
`xmlParseDocument`, the lexer and SAX callbacks, `xmlFreeDoc`,
`xmlCleanupParser`.

The harness's `ignore` handler is reachable through an indirect edge. The
harness passes `&ignore` to `xmlSetGenericErrorFunc`, and libxml2 later calls
the handler through a function pointer. It rates `medium`: there is value-flow
evidence that its address is called, not only a type match. Being a C++
function, it appears under its mangled name. The harness was compiled without
`-g`, so `file` and `line` are empty:

```json
{
  "mangled": "_Z6ignorePvPKcz",
  "demangled": "ignore(void*, char const*, ...)",
  "key": "_Z6ignorePvPKcz",
  "file": null,
  "line": null,
  "via": "indirect",
  "indirect_only": true,
  "confidence": "medium",
  "depth": 1,
  "basic_blocks": 1,
  "dangerous_calls": 0,
  "local_vars": 2,
  "cyclomatic": 1,
  "loops": 0,
  "interesting": true,
  "bottleneck": false,
  "dead_end": true
}
```

A directly reached function, with its source location:

```json
{
  "mangled": "xmlReadMemory",
  "demangled": "xmlReadMemory",
  "key": "xmlReadMemory",
  "file": "parser.c",
  "line": 15376,
  "via": "direct",
  "indirect_only": false,
  "confidence": "high",
  "depth": 1,
  "basic_blocks": 4,
  "dangerous_calls": 0,
  "local_vars": 1,
  "cyclomatic": 2,
  "loops": 0,
  "interesting": true,
  "bottleneck": false,
  "dead_end": false
}
```

All fields are described in [output.md](output.md#function-objects).

**Unreachable** (`not_reached.txt`): the large parts of libxml2 the harness
never enters, such as the XPath evaluator (`xmlXPathEval`,
`xmlXPathCompile`), schema and RelaxNG validation (`xmlSchemaValidateDoc`,
`xmlRelaxNGValidateDoc`), and the serialization and writer API
(`xmlSaveFile`, `xmlTextWriterStartDocument`). With `--static-libs none` most of
these would be missing from the report instead of unreachable.

> **Over-approximation at work.** `xmlXPathEval` is unreachable, yet XPath
> internals such as the axis function `xmlXPathNextAncestor` are reachable: 59
> reachable indirect call sites have its type, `ptr (ptr, ptr)`, which many
> callbacks share. XPointer helpers such as `xmlXPtrRangeToFunction` are
> reached the same way and call `xmlXPathEvalExpr` directly. The analysis
> accepts such false edges so that it does not miss real ones.

### Step 5 — Instrument only reachable code

Pass the ignorelist to the coverage build, so that SanitizerCoverage skips the
code the harness cannot reach:

```bash
clang -fsanitize-coverage=trace-pc-guard -fsanitize-coverage-ignorelist=not_reached.txt ...
```

`-fsanitize-coverage-allowlist=reached.txt` is the alternative; see
[Allowlist or ignorelist](output.md#allowlist-or-ignorelist).

### Step 6 — Compare with LTO

LTO already removes functions that nothing references. To see what that means
for the lists, build the same library and harness as a libFuzzer binary with
full LTO, in a fresh copy of the sources next to the analyzed one:

```bash
mkdir lto && tar xf libxml2-2.9.2.tar.gz -C lto && cd lto/libxml2-2.9.2
cp ../../libxml2-2.9.2/harness.cc .
CC=clang-23 CXX=clang++-23 AR=llvm-ar-23 RANLIB=llvm-ranlib-23 NM=llvm-nm-23 \
  CFLAGS='-O2 -flto=full' LDFLAGS='-flto=full -fuse-ld=lld' \
  ./configure --without-python --disable-shared
make -j"$(nproc)" libxml2.la
clang++-23 -O2 -flto=full -fuse-ld=lld -fsanitize=fuzzer -I include harness.cc \
  .libs/libxml2.a -lz -llzma -lm -o harness
llvm-nm-23 --defined-only harness | awk '$2 ~ /^[tTwW]$/ {print $3}' > funcs.txt
grep '^fun:' ../../libxml2-2.9.2/not_reached.txt | cut -c5- | grep -Fxc -f funcs.txt
```

The last command prints how many functions from `not_reached.txt` are still in
the binary. The same with `-flto=thin`, and with no LTO (`-O2` and
`-fuse-ld=lld` only):

| Build | Defined text symbols | Left from `not_reached.txt` (1,134) | Left from `reached.txt` (1,901) |
|---|---|---|---|
| `-O2 -flto=full` | 1,871 | 0 | 560 |
| `-O2 -flto=thin` | 2,100 | 0 | 719 |
| `-O2` | 3,590 | 797 | 1,477 |

With LTO no unreachable function is left, so on this target the ignorelist
changes nothing for an LTO build. Without LTO, 797 unreachable functions stay in
the binary and are instrumented unless the ignorelist excludes them. The
reached counts drop under LTO because of inlining. The `reached.txt` column
counts exact names only; thin LTO renames some functions to `….llvm.N`.

Build problems with this example, such as `gclang: not found` or overlapping
archives, are covered in [troubleshooting.md](troubleshooting.md).

---

## 2. The `url` crate + a ziggy harness (Rust)

The [ziggy](https://github.com/srlabs/ziggy) repository has an example harness
that fuzzes the `url` crate in several ways (`examples/url/src/main.rs`, at
ziggy commit `7af4467`). Its fuzz loop is inside `fn main()`:

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

A ziggy harness has no `LLVMFuzzerTestOneInput`; the entry is the Rust `main`.
`--lang ziggy` starts there automatically.

### Prerequisites

A built analyzer, a nightly rustc whose LLVM is not newer than the analyzer's
(here LLVM 22.1.8 against 23), and cargo-ziggy. `--lang ziggy` builds through
cargo-afl, so the AFL++ plugins must be built for the current rustc:
`LLVM_CONFIG=llvm-config-22 cargo afl config --build --plugins --force`.

### Step 1 — Get the harness

```bash
git clone https://github.com/srlabs/ziggy
cd ziggy
```

### Step 2 — Run the analysis

```bash
reachability run --lang ziggy --project examples/url --clean -v
```

- **`--lang ziggy`** builds with `cargo ziggy build --no-honggfuzz`, with a
  `RUSTC_WRAPPER` that adds `--emit=llvm-bc` and collects each crate's bitcode.
  It reads the bitcode from rustc's output directories, so it does not matter
  that `examples/url` is a workspace member whose target directory is at the
  workspace root.
- **The bitcode matches the fuzz binary**, because it comes from ziggy's own
  build with the same `cfg(fuzzing)`, features and instrumentation.
- **`--clean`** runs `cargo clean` first, so a cached build cannot leave the
  run without bitcode.

See [rust.md](rust.md#native-harness-builds) for the details.

### Step 3 — Read the report

```
reachable 9073 / defined 19717  (756 indirect-only, 128 low-confidence, 10644 unreachable)  [backend=type-based]
```

**Starting from the Rust `main`.** The token `main` resolves to two symbols,
shown in the JSON `entries`:

```json
"entries": ["main", "_RNvCscjF3EmfBC9s_8url_fuzz4main"]
```

The first is the C `main` that starts the Rust runtime. The second is the real
`url_fuzz::main` with the `ziggy::fuzz!` body. The names are v0-mangled
(`"mangling": "v0"`), the default of rustc 1.99.

**Reachable**: the harness and the `url` parser it drives: `url_fuzz::main`,
all five strategies (`invariant_fuzz`, `differential_fuzz`, `correctness_fuzz`,
`consistency_fuzz`, `idempotency_fuzz`), and `url::ParseOptions::parse` with
the parser below it.

**Unreachable** (`not_reached.txt`): API the harness never calls. The harness
only parses URLs, so the mutation API is unreachable: `url::Url::set_scheme`,
`set_host`, `set_port`, `set_path`, `set_query`, `set_fragment`,
`set_username`, `set_password`, and their internal helpers. Most of the
Unicode and IDNA code is unreachable too, for example 540 of the 562
`icu_properties` functions.

> **Over-approximation is heavier in Rust.** 9,073 of 19,717 functions are
> reachable, because Rust calls through trait-object vtables everywhere. Every
> address-taken function whose type matches a reachable indirect call is a
> candidate, so once any `Debug::fmt`-shaped call is reachable, `fmt`
> implementations across the dependencies come along; for example 2,533
> functions of `syn` are reachable. `indirect-only` (756) counts the functions
> reached only this way.

---

## 3. cpp_demangle + a cargo-afl harness (Rust)

[cpp_demangle](https://github.com/gimli-rs/cpp_demangle) is a Rust demangler
for C++ (Itanium) symbols. At commit `076632f` it has a cargo-afl harness in
`src/bin/afl_runner.rs` that feeds the fuzz input to the parser:

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

Like ziggy, a cargo-afl harness keeps its fuzz loop in `main`. The binary is
feature-gated (`required-features = ["afl"]`), so `cargo afl build` alone skips
it. Pass the feature and the binary with `--build-cmd`.

### Step 1 — Get it

```bash
git clone https://github.com/gimli-rs/cpp_demangle
cd cpp_demangle
```

### Step 2 — Run the analysis

```bash
reachability run --lang afl --project . \
  --build-cmd 'cargo afl build --features afl --bin afl_runner' --clean -v
```

This needs the AFL++ plugins, as in [§2](#prerequisites-1).

### Step 3 — Read the report

```
reachable 1388 / defined 2436  (189 indirect-only, 46 low-confidence, 1048 unreachable)  [backend=type-based]
```

`main` again resolves to the C `main` and the Rust main:
`"entries": ["main", "_RNvCshnZqb9Nwllb_10afl_runner4main"]`.

**Reachable**: `afl_runner::main`, the `afl::fuzz!` closure,
`cpp_demangle::Symbol::new`, and the recursive-descent grammar below it,
including the `SubstitutionTable` inserts that record back-reference
candidates during the parse. 815 of the 1,388 come from the Rust runtime
(`core`, `alloc`, `std`), instantiated for the parser.

```json
{
  "mangled": "_RNvMs0_Cs6Asx3OJCf2Q_12cpp_demangleINtB5_6SymbolRShE3newCshnZqb9Nwllb_10afl_runner",
  "demangled": "<cpp_demangle::Symbol<&[u8]>>::new",
  "key": "_RNvMs0_Cs6Asx3OJCf2Q_12cpp_demangleINtB5_6SymbolRShE3new",
  "file": "src/lib.rs",
  "line": 179,
  "via": "direct",
  "indirect_only": false,
  "confidence": "high",
  "depth": 8,
  "basic_blocks": 9,
  "dangerous_calls": 1,
  "local_vars": 0,
  "cyclomatic": 3,
  "loops": 0,
  "interesting": true,
  "bottleneck": true,
  "dead_end": false
}
```

`Symbol::new` is generic, and this instance was compiled in `afl_runner`. The
`key` drops that trailing crate (`…Cshn…_10afl_runner`), so it also matches the
instance another build compiles elsewhere; see
[Rust names](output.md#rust-names-and-key).

**Unreachable** (`not_reached.txt`): the code that only runs when a symbol is
printed, because the harness parses but never demangles. This includes the
substitution lookups (`SubstitutionTable::get_type`, `pop`,
`non_substitution`), the back-reference resolvers (`TypeHandle::back_reference`,
`TemplateParam::resolve`, …), accessors only used while printing
(`BareFunctionType::args` and `ret`), and the options builder
(`DemangleOptions::new`, `no_params`, `recursion_limit`, …).

> **Unused generic code is absent, not unreachable.** `Symbol::demangle`, the
> `Demangle` implementations and the `Display` code appear in neither list.
> They are generic and nothing in this build instantiates them, so rustc never
> compiles them. The analysis can only classify functions that exist.
> cpp_demangle's cargo-fuzz harness `fuzz/fuzzers/parse_and_stringify.rs` does
> call `sym.demangle()`; analyze that one and the printing code appears.

> **The indirect-only functions are mostly formatting.** 131 of the 189
> indirect-only functions are `fmt` implementations, reached because a
> formatting call of the same type is reachable.

---

## 4. rustyknife + a cargo-afl harness (Rust)

[rustyknife](https://github.com/zerospam/rustyknife) is an email-parsing
library. At commit `1f652c4` it has a cargo-afl harness in
`src/bin/fuzz_mailbox.rs`:

```rust
#[macro_use]
extern crate afl;

fn main() {
    fuzz!(|data: &[u8]| {
        let _ = rustyknife::rfc5321::mailbox::<rustyknife::behaviour::Intl>(data);
    });
}
```

Three things need care:

- The `fuzz_mailbox` binary is feature-gated (`required-features = ["fuzz"]`).
- rustyknife's `rust-toolchain` file selects `stable`. Here that is rustc 1.99.0
  with LLVM 23.1.1, newer than the analyzer's LLVM 23.0.0, so the run would
  stop with `rustc's bundled LLVM (23.1.1) is newer than the analyzer
  toolchain's LLVM (23.0.0)`. `RUSTUP_TOOLCHAIN=nightly` picks the nightly
  instead.
- rustyknife uses the old `afl` 0.8 crate, whose bundled AFL fails a modern
  compiler's x86 self-test and stops on warnings. Only the bitcode matters, so
  skip the self-test with `AFL_NO_X86=1` and add `-Wno-error` with small
  compiler wrappers.

### Step 1 — Get it

```bash
git clone https://github.com/zerospam/rustyknife
cd rustyknife
```

### Step 2 — Run the analysis

```bash
mkdir -p /tmp/nowerror
for t in cc clang clang++; do
  printf '#!/bin/sh\nexec /usr/bin/%s "$@" -Wno-error\n' "$t" > /tmp/nowerror/$t
  chmod +x /tmp/nowerror/$t
done
export PATH="/tmp/nowerror:$PATH" CC=/tmp/nowerror/cc AFL_NO_X86=1 RUSTUP_TOOLCHAIN=nightly

reachability run --lang afl --project . \
  --build-cmd 'cargo afl build --features fuzz --bin fuzz_mailbox' --clean -v
```

### Step 3 — Read the report

```
reachable 2933 / defined 17819  (438 indirect-only, 172 low-confidence, 14886 unreachable)  [backend=type-based]
```

`"entries": ["main", "_RNvCs5n6KfqIeUf6_12fuzz_mailbox4main"]`, as before.

**Reachable**: `fuzz_mailbox::main`, the `fuzz!` closure,
`rustyknife::rfc5321::mailbox::<rustyknife::behaviour::Intl>`, the RFC 5321
grammar rules such as `dot_string`, and the `nom` parser combinators below
them (194 `nom` functions).

**Unreachable**: rustyknife's other parsers, which this harness never calls:
the RFC 2047 decoder (`rustyknife::rfc2047::decode_text`) and most of the RFC
5322 message grammar (48 `rustyknife::rfc5322` functions). Five small
`rfc5322` helpers that the RFC 5321 grammar shares are reachable.

> **A focused harness gives a focused report.** Only 2,933 of 17,819 functions
> (16%) are reachable. The harness goes through a single parser entry, so the
> over-approximation stays small, unlike the `url` example (46%), where
> trait-object calls pull in much more.
