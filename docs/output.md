# Output reference

`reachability run` writes these files:

| File | Default path | Option | Content |
|---|---|---|---|
| JSON report | `reachability.json` in `--project` | `--out` | Reachable and unreachable functions, counts, metrics, call graph |
| Allowlist | `reached.txt` next to the report | `--reached` | Reachable functions as a coverage list |
| Ignorelist | `not_reached.txt` next to the report | `--not-reached` | Unreachable functions as a coverage list |
| DOT graph | none | `--dot` | The reachable call graph for Graphviz |

How the files are staged and replaced is described in
[usage.md](usage.md#output-files).

## JSON report

The current schema version is 2. `reachable`, `unreachable_defined`,
`external_declarations` and `escaped_from_unreached` are sorted by IR name.

Functions are named in two ways:

- **`mangled`** is the symbol name in the object file. It is what clang
  sancov and AFL++ match against, and what the coverage lists contain.
- **The IR name** is the name in the merged module. It is unique within the
  report. `edges`, `entries`, `external_declarations` and
  `escaped_from_unreached` use it. It differs from `mangled` only for renamed
  `static` functions (see [below](#static-functions-with-the-same-name)), and
  then the function object carries it as `ir_name`.

### Top-level keys

| Key | Type | Meaning | Added in |
|---|---|---|---|
| `schema_version` | integer | `2`. A report without this key is version 1. | 1.2 |
| `llvm_version` | string | LLVM major version the analyzer was built with | 1.0 |
| `backend` | string | Always `"type-based"` | 1.0 |
| `mangling` | string | `"v0"` if a defined function has a Rust v0 name (`_R…`), else `"legacy"` | 1.1 |
| `run` | object | The options of the run, see [`run`](#run). Missing when the analyzer runs without `--run-info`. | 1.2 |
| `entries` | array of strings | Every root the analysis started from, by IR name | 1.0 |
| `summary` | object | Counts, see [`summary`](#summary) | 1.0 |
| `reachable` | array of objects | Reachable defined functions, see [function objects](#function-objects) | 1.0 |
| `unreachable_defined` | array of objects | Defined functions that are not reachable | 1.0 |
| `external_declarations` | array of strings | Reachable functions without a body in the bitcode | 1.1 |
| `escaped_from_unreached` | array of strings | Unreachable functions that only unreached code hands to external code | 1.2 |
| `edges` | array of objects | The reachable call graph, see [`edges`](#edges). Left out with `--no-edges`. | 1.1 |

`entries` includes the roots the analysis adds on its own:
`LLVMFuzzerInitialize`, functions found by
[runtime name lookup](analysis.md#runtime-name-lookup), and
[process-lifecycle roots](usage.md#process-lifecycle-roots).

`external_declarations` lists code the analysis could not see: system
libraries, precompiled libraries, the Rust standard library without
`--build-std`, and assembly-only units. LLVM intrinsics are not listed. Tests:
`test_analyzer_core.py::test_reachable_external_declarations` and
`::test_indirect_external_declaration_is_reported`.

`escaped_from_unreached` usually means the harness skips an initialization
path that the real program runs. See
[Limitations](analysis.md#limitations). Test:
`test_analyzer_core.py::test_callbacks_handed_out_only_by_unreached_code_are_reported`.

### `run`

The driver fills this object. Running the analyzer by hand leaves it out
unless you pass `--run-info`.

| Key | Type | Meaning |
|---|---|---|
| `tool_version` | string | Driver version, for example `"1.2-dev"` |
| `lang` | string | `--lang` |
| `entries` | array of strings | `--entry` values, or the `--lang` default |
| `optimize` | boolean | `--optimize` |
| `include_process_lifecycle_roots` | boolean | `true` by default; `false` with `--no-process-lifecycle-roots` |
| `no_name_roots` | boolean | `--no-name-roots` |
| `artifact` | string or null | `--artifact`; only for `c`, `cpp` and `mixed` |
| `static_libs` | string | `--static-libs`; only for `c`, `cpp` and `mixed` |
| `profile` | string or null | `--profile`; only for Rust and `mixed` |
| `codegen_units` | integer or null | `--codegen-units`; only for Rust and `mixed` |
| `mangling` | string | `--mangling`; only for Rust and `mixed` |
| `build_std` | boolean | `--build-std`; only for Rust and `mixed` |

Test: `test_analyzer_core.py::test_run_info_and_edges_toggle`.

### `summary`

| Key | Type | Meaning | Added in |
|---|---|---|---|
| `defined` | integer | Defined functions in the merged module | 1.0 |
| `reachable` | integer | Length of `reachable` | 1.0 |
| `unreachable` | integer | Length of `unreachable_defined` | 1.0 |
| `indirect_only` | integer | Reachable functions that only indirect calls reach | 1.0 |
| `low_confidence` | integer | Reachable functions with `confidence` `low` | 1.0 |
| `external_declarations` | integer | Length of `external_declarations` | 1.1 |
| `escaped_from_unreached` | integer | Length of `escaped_from_unreached` | 1.2 |
| `compile_units` | integer | Debug-info compile units in the module | 1.2 |
| `optimized_compile_units` | integer | Compile units whose debug info is marked `isOptimized` | 1.2 |
| `no_inline_definitions` | integer | Definitions marked `noinline` or `optnone` | 1.2 |

The last three are the evidence for the
[post-optimizer warning](troubleshooting.md#the-analyzed-bitcode-was-built-with-optimization-enabled).
Tests: `test_analyzer_core.py::test_build_optimization_evidence_optimized`,
`::test_build_optimization_evidence_source_faithful` and
`::test_build_optimization_evidence_without_debug_info`.

### Function objects

`reachable` and `unreachable_defined` hold one object per function. Entries in
`unreachable_defined` have only the first six keys.

| Key | Type | Meaning | Added in |
|---|---|---|---|
| `mangled` | string | Symbol name in the object file | 1.0 |
| `ir_name` | string | Name in the merged module; present only when it differs from `mangled` | 1.2 |
| `demangled` | string | Demangled name (C++ Itanium, Rust legacy and v0) | 1.0 |
| `key` | string | `mangled` without the build-dependent part of a Rust name, see [Rust names](#rust-names-and-key) | 1.1 |
| `file` | string or null | Source file from debug info | 1.0 |
| `line` | integer or null | Source line from debug info | 1.0 |
| `via` | string | `direct`, `indirect` or `both`: the kinds of call edge that reach the function | 1.0 |
| `indirect_only` | boolean | `true` when `via` is `indirect` | 1.0 |
| `confidence` | string | `high`, `medium` or `low`, see [Confidence](analysis.md#confidence) | 1.0 |
| `depth` | integer | Fewest call edges from an entry; entries are `0` | 1.1 |
| `basic_blocks` | integer | See [Function metrics](analysis.md#function-metrics) | 1.1 |
| `cyclomatic` | integer | See [Function metrics](analysis.md#function-metrics) | 1.1 |
| `loops` | integer | See [Function metrics](analysis.md#function-metrics) | 1.1 |
| `dangerous_calls` | integer | See [Function metrics](analysis.md#function-metrics) | 1.1 |
| `local_vars` | integer | See [Function metrics](analysis.md#function-metrics) | 1.2 (named `C11` in 1.1) |
| `interesting` | boolean | See [Function metrics](analysis.md#function-metrics) | 1.1 |
| `bottleneck` | boolean | See [Function metrics](analysis.md#function-metrics) | 1.1 |
| `dead_end` | boolean | See [Function metrics](analysis.md#function-metrics) | 1.1 |

Symbol names that are not valid UTF-8 are written with replacement
characters. Test: `test_analyzer_core.py::test_invalid_utf8_symbol_is_sanitized`.

Example, from the libxml2 run in [EXAMPLES.md](EXAMPLES.md#1-libxml2--a-libfuzzer-harness-cc):

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

### `edges`

Each edge is `{"from": …, "to": …, "kind": …}`. `from` and `to` are IR names,
and `kind` is `direct` or `indirect`. Only edges between two reachable defined
functions are listed, sorted by `from` and then `to`. The array grows with call
sites times indirect targets, so on code with large dispatch tables it
dominates the report size and run time. `--no-edges` leaves it out.

Tests: `test_analyzer_core.py::test_json_edges_reachable_only`,
`::test_json_edges_reference_listed_nodes`, `::test_json_depth_min_path` and
`::test_large_call_graph_output_is_deterministic`.

### Schema versions

| Schema | Tool version | Changes |
|---|---|---|
| 1 (no `schema_version` key) | 1.0 | First format |
| 1 (no `schema_version` key) | 1.1 | Adds `key`, `depth`, the function metrics (with `C11`), `mangling`, `external_declarations` and `edges` |
| 2 | 1.2 | Adds `schema_version`, `run`, `ir_name`, `escaped_from_unreached`, `compile_units`, `optimized_compile_units` and `no_inline_definitions`; renames `C11` to `local_vars` |

The key set is pinned by `test_analyzer_core.py::test_json_schema_contract`.

## Coverage lists

`reached.txt` and `not_reached.txt` use the SanitizerCoverage list format. clang
reads them with `-fsanitize-coverage-allowlist=` and
`-fsanitize-coverage-ignorelist=`. AFL++ reads the same files through
`AFL_LLVM_ALLOWLIST` and `AFL_LLVM_DENYLIST`
(see AFL++'s `instrumentation/README.instrument_list.md`).

Both lists hold `fun:` lines with `mangled` names, so they work for C, C++ and
Rust. They also differ in three ways:

- **`reached.txt` starts with `src:*`.** An allowlist instruments a function
  only when both a `src:` line and a `fun:` line match. An ignorelist has no
  such rule, so `not_reached.txt` has only `fun:` lines.
- **`reached.txt` lists every name a reachable function can have:** its
  `mangled` name and its IR name. For a renamed `static` function without debug
  info it also lists every name with the `.N` suffixes removed. This can only
  add instrumentation.
- **`not_reached.txt` leaves out any pattern that would also match a
  reachable function**, so excluding unreachable code never excludes reachable
  code.

Lines starting with `#` are comments.

Tests: `test_covlists.py::test_c_allowlist_and_ignorelist` and
`::test_cpp_allowlist_matches_mangled` (both checked against clang),
`test_analyzer_core.py::test_coverage_lists` and
`::test_ignorelist_glob_never_excludes_reachable`.

### Allowlist or ignorelist

Both lists come from the same reachable set. They differ for a reachable
function that has no body in the analyzed bitcode, such as one from a
precompiled library, the Rust standard library without `--build-std`, or an
assembly-only unit. Neither list names such a function; the report lists it in
`external_declarations`.

- **With the allowlist**, the coverage build does not instrument it, because
  it is not in `reached.txt`. If the coverage build compiles that code from
  source, for example Rust's standard library with `-Zbuild-std`, the function
  runs without coverage.
- **With the ignorelist**, it stays instrumented, because it is not in
  `not_reached.txt` either. This only helps if the coverage build compiles that
  function from source. A precompiled library or an assembly-only unit is not
  instrumented under either list.

Use the ignorelist. It never excludes more than the allowlist would, so it
cannot create a blind spot on its own. A nonzero
`summary.external_declarations` tells you that part of the reachable code lies
outside the analyzed bitcode. `--static-libs auto`, `--build-std` and a build
without LTO make that part smaller, but cannot remove code that was never
compiled to bitcode.

### Rust names and `key`

Part of a Rust generic instance's name depends on the build, not the source:

- **legacy mangling:** the trailing `17h<hash>` disambiguator;
- **v0 mangling:** the trailing instantiating crate (`Cs<hash>_<len><crate>`,
  or a `B<n>_` back-reference to a crate named earlier).

Both change when another crate instantiates the generic. At opt-level 0 a crate
reuses a dependency's copy of `dep::checksum::<u8>` (`…8checksumhEB2_`,
instantiated in `dep`), while an optimized build compiles its own
(`…8checksumhECs…_21rust_upstream_generic`). An exact name from the analysis
build can therefore miss the instance that the fuzz binary runs.

So the report and the lists drop that part:

- `key` is `mangled` without it. For every other name `key` equals `mangled`.
- The lists replace it with `*`, which clang and AFL++ treat as a glob. A v0
  path ends in a fixed place, so `<path>*` only matches instances of that one
  function, never a different function whose name starts the same way.
- Names with a vendor suffix after the instantiating crate (`.llvm.<n>`,
  `.cold`) stay exact.

This only fixes the name of a given instance. Whether both builds contain the
same instances, and whether legacy and v0 names can be joined at all, is
covered in [rust.md](rust.md#mangling).

Tests: `test_analyzer_core.py::test_json_key_strips_rust_disambiguator`,
`::test_json_key_strips_rust_v0_instantiating_crate`, and
`test_rust_hash_stability.py`, which builds `fixtures/rust_upstream_generic` at
two opt levels under both schemes and checks that the keys agree and that the
opt-level-0 entry matches the optimized name.

### Static functions with the same name

Two translation units can each define a `static` (or anonymous-namespace)
function with the same name. `llvm-link` must keep names unique, so it renames
one copy (`helper` becomes `helper.1`). Each object file still uses the
original name, and that is the name sancov and AFL++ match.

The analyzer takes the original name from debug info (the `DISubprogram`
linkage name or name) and reports it as `mangled`, with the merged name in
`ir_name`. The allowlist lists every reachable copy under both names. The
ignorelist leaves out any name that a reachable copy also has, so excluding one
unreachable copy never excludes a reachable one. Without debug info the
original name cannot be recovered; `mangled` then stays the merged name and the
allowlist also lists the name with `.N` removed.

Tests: `test_analyzer_core.py::test_renamed_static_function_keeps_both_lists_safe`
and `test_covlists.py::test_static_name_collision_stays_instrumented`.

## How cov-analysis joins

[cov-analysis](https://github.com/AFLplusplus/cov-analysis) reads the JSON
report, a directory that contains it, or the two lists. It matches each
function in its coverage data against the report in this order:

1. for a `static` function, which `llvm-cov` names `<file>:<symbol>`, the pair
   of file name and `mangled`;
2. the exact name, `mangled` or `demangled`;
3. the `key`, which cov-analysis computes the same way from its own names;
4. JSON only: the source `file` and `line`.

The lists only support steps 2 and 3, so prefer the JSON report.

## DOT graph

`--dot FILE` writes the same subgraph as `edges` in Graphviz format. Indirect
edges are dashed and red. Functions without a body are not nodes; they stay in
the JSON `external_declarations`. The output is deterministic.

Tests: `test_analyzer_core.py::test_dot_export` and
`::test_dot_export_is_deterministic_and_excludes_declarations`.
