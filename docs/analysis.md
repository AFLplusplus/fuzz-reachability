# How the analysis works

This page explains how the reachable set is computed, what the confidence and
metric fields mean, and where the analysis is known to fall short.

## Pipeline

```
   driver (Python)                   analyzer (C++ / LLVM)
   ───────────────                   ─────────────────────
   acquire bitcode ─┐
   C/C++ : gllvm    │
   Rust  : rustc    │   llvm-link    load .bc → build call graph →
   --emit=llvm-bc   ├─► merge .bc ─► resolve indirect calls → BFS from
                    │                entry → JSON report + coverage lists
   or, with         │
   --capture rllvm: │
   C/C++/Rust from ─┘
   one build
```

The **driver** (`driver/reachability/`) checks that the LLVM tools agree on one
version, builds the project so that it emits bitcode, merges all bitcode with
`llvm-link`, and runs the analyzer. How it builds each kind of project is in
[usage.md](usage.md) and [rust.md](rust.md).

The **analyzer** (`analyzer/src/`) works on the merged module in these steps:

1. Load the module and count each function's local variables.
2. Promote stack slots to registers (`mem2reg`). This deletes no function. It
   lets the value flow follow pointers through `-O0` code, where every pointer
   is reloaded from its own stack slot.
3. Add a direct edge for every call that resolves to a function, through
   casts and aliases. Callback operand bundles and defined personality
   functions also add edges.
4. Add edges for callbacks handed to external code
   ([Escapes](#callbacks-handed-to-external-code)).
5. Add edges for indirect calls ([Indirect calls](#indirect-calls)).
6. Collect the roots: the entries, process constructors and destructors, ifunc
   resolvers, `LLVMFuzzerInitialize`, and functions found by
   [runtime name lookup](#runtime-name-lookup). The narrower
   `--no-process-lifecycle-roots` mode still includes callbacks that
   constructors hand out ([usage.md](usage.md#process-lifecycle-roots)).
7. Walk the call graph breadth-first from the roots. The walk also records
   each function's `depth`.
8. Grade each reachable function's [confidence](#confidence), compute the
   [function metrics](#function-metrics), and write the outputs.

The analyzer demangles C++ (Itanium) and Rust (legacy and v0) names.

Tests: `test_analyzer_core.py::test_direct_edge_detected`,
`::test_alias_chains_resolve_to_direct_callee`,
`::test_operand_bundle_callback_is_reachable`,
`::test_defined_personality_is_a_reachable_edge` and
`::test_heap_callback_with_mismatched_type_is_reachable_at_O0`.

## Call graph

Functions without a body are leaf nodes: they can be reached, but have no
outgoing edges. Indirect call sites do not get one edge per candidate. They
point at shared target sets, so a dispatch table with N targets used from M
sites costs N + M instead of N × M. The JSON `edges` array still lists every
pair, which is why `--no-edges` exists.

## Indirect calls

Indirect calls are C function pointers, C++ virtual calls, and Rust `dyn` and
`fn` pointers. The analyzer resolves them by type: a call of function type `T`
can reach any address-taken function whose LLVM type is `T`.

The analyzer also tracks where function addresses flow. A function whose
address reaches the call site is added even when a cast changed its type. When
the flow loses track of a value across a cast, for example through an integer,
that call site can reach every address-taken function. Declarations follow the
same rules as definitions.

The address flow follows values through globals, aggregates, `select` and PHI
nodes, casts, call arguments and return values, and memory. A load sees what
was stored into the same underlying object. A store is a plain store, an atomic
exchange, the new value of a compare-exchange, or a `memcpy` or `memmove` from
another object.

**Setters.** When a function stores a callback through one of its pointer
parameters, such as `set_cb(obj, fn)`, the store also counts for every object
its callers pass there. Here a callback is:

- a function, or a pointer parameter;
- a `select` or PHI that picks one;
- a load from a struct field that some store or initializer fills with a
  function;
- a load at a byte offset (as in Rust and optimized C) from an object that
  holds a callback;
- a pointer to such an object.

An object holds a callback when an initializer or store puts a function into
it, or a parameter that callers pass a function (`init(&t, cb)`), or when it is
copied from such an object (`memcpy`, struct assignment). This also works
through helpers and constructors, and for globals, locals and objects a caller
passes in. Other values, vtable pointers in particular, are not propagated.
That keeps C++ `this` passing from merging the fields of every object.

`--indirect-any` on the analyzer widens every indirect call to all
address-taken functions. It is a debugging aid.

Tests: `test_analyzer_core.py::test_typebased_indirect`,
`::test_type_punned_flow_is_unioned_with_exact_candidates`,
`::test_exact_indirect_control_stays_narrower_than_indirect_any`,
`::test_indirect_any_includes_other`,
`::test_partial_integer_laundering_falls_back_conservatively`,
`::test_resolved_integer_roundtrip_does_not_force_maximal_fallback`,
`::test_stored_integer_laundering_falls_back_conservatively`,
`::test_setters_that_copy_or_choose_a_callback` and
`::test_callback_in_object_passed_to_indirect_call`. The property tests in
`test_soundness_property.py` compare random modules against an oracle.

## Callbacks handed to external code

Code without a body in the bitcode (libc, a precompiled library, the Rust
standard library) can call a function the program hands it: a `qsort`
comparator, an `atexit` or `pthread_create` callback, an error hook. The call
graph cannot see that call, so the analyzer adds an edge from the function that
hands the callback out to the callback itself. The callback is then reachable
whenever that function is.

A callback is handed out when it is:

- an argument to a call of a function without a body, to an indirect call, or
  to inline asm;
- returned;
- stored, or copied with `memcpy`, into memory that external code owns.

Calls to LLVM intrinsics (`memcpy`, lifetime markers, bit counting, …) do not
hand anything out, unless a function is passed to them directly
(`llvm.init.trampoline`).

Memory is owned by external code when it is:

- a global that is only declared in the bitcode;
- what a function without a body returns;
- what such a function writes through a pointer argument, such as
  `ext_get_hooks(&h)`. This also holds when the pointer is loaded again, as in
  `ext_get_hooks(g_ctx); g_ctx->hooks->cb = f`;
- what a callback that external code calls receives as a pointer parameter;
- memory reached by loading pointers out of any of these, or out of memory
  where the program stored such a pointer (`g_lib = lib_init(); g_lib->cb = f`).

Defined wrappers, accessors and helpers are followed through their return
values and parameters at any depth, also when they pick the pointer with `?:`
or return early. Each call is judged by its own arguments, so an accessor that
some callers pass external memory does not make the memory of other callers
external.

Memory is the program's own when it comes from an allocator (`malloc`,
`calloc`, `realloc`, `strdup`, `operator new`, and declarations whose result is
`noalias` or marked as an allocation), or when a known C library function such
as `strtol` writes it.

Tests: `test_analyzer_core.py::test_stores_into_escaping_memory_objects`,
`::test_external_callback_loaded_from_local`,
`::test_callbacks_in_memory_owned_by_external_code`,
`::test_external_memory_through_wrappers_helpers_and_out_parameters` and
`::test_callbacks_handed_out_before_the_first_input`.

## Runtime name lookup

Code can find a function by name at run time with `dlsym`, `dlvsym`, `dlopen`,
`dlmopen` or `GetProcAddress`, and then call it. Such a function has no caller
and no taken address. Its only link to the program is a string constant with its
name.

When the module calls one of these lookup functions, the analyzer makes every
defined, externally visible function whose symbol name appears verbatim as a
string constant a root. `internal` functions are skipped, because `dlsym` only
finds symbols in the dynamic symbol table. A module that never looks up names
gets no such roots.

`--no-name-roots` turns this off, on both `reachability run` and the analyzer.
Turning it off can under-report. It is there to measure how much the heuristic
adds, not to make the result more precise.

Tests: `test_analyzer_core.py::test_dlsym_byname_root`,
`::test_dlsym_byname_disabled`, `::test_name_root_gated_on_dynamic_lookup`,
and `test_analyze.py::test_name_roots_flag_changes_reachable_set`.
`fixtures/rust_indirect` has a `dlsym`-resolved `#[no_mangle]` target.

## Confidence

Type matching adds some functions only because a function of their type is
called somewhere and their address was taken, not because their address is
shown to reach a call. Each reachable function therefore has a `confidence`:

- **`high`**: a direct call reaches it, or it is a root.
- **`medium`**: only indirect calls reach it, but there is evidence that its
  address is used as a callable. The evidence is one of:
  - the address flows to the callee operand of an indirect call;
  - it is handed to external code that may call it (`qsort`, `signal`,
    `pthread_create`, …);
  - it is stored into the same slot that a reachable indirect call loads from:
    the same struct field (struct type and field path), or the same vtable slot
    (byte offset from the vtable's address point).
- **`low`**: only a type match reaches it. No flow evidence was found, for
  example for a function whose address is only taken, cast to an integer, and
  stored, but never called.

Vtable slots are matched by kind. A C++ vtable slot matches only a call that
loads the vtable pointer out of the object it passes as `this`, in C++ code or
code of unknown language. A Rust vtable slot matches only a call in Rust code.
A C call built without debug info has the C++ shape when it passes the object
it loaded a function table from, as in `o->fns[1](o, …)` or, in optimized C,
`o->ops->f(o, …)`. It then counts as evidence for that slot of every C++ vtable.

Evidence is only collected inside reachable functions. Inline asm and
intrinsics do not count as callers: `std::hint::black_box` lowers to an empty
`asm sideeffect ""`, and `llvm.lifetime`, `memcpy` and `dbg` only observe or
move a value. A `memcpy` into externally owned memory counts as a store there.

Confidence is a hint for triage. It never changes the reachable set or the
lists: a `low` function stays in `reached.txt`. A real target can rate `low`,
for example when it is called only from inline asm or only as a constructor,
and a decoy whose address escapes to a real sink can rate `medium`. Read `low`
as "worth a look", not as "safe to remove".

Tests: `test_analyzer_core.py::test_confidence_tiers`,
`::test_confidence_evidence_is_entry_relative`,
`::test_slot_evidence_grades_confidence` and
`::test_vtable_slots_match_only_their_own_dispatch`.

## Function metrics

Each reachable function carries static hints for deciding where a fuzzing
campaign should focus. Like `confidence`, they never change the reachable set
or the lists.

By default the metrics describe the functions as written, because the
analysis build does not optimize. With `--optimize` they describe the
optimized code instead: the basic blocks, loops, local variables and dangerous
calls of an inlined callee then count for its caller, so an `-O3` `main` can
report several times the counts of its source.

| Field | Meaning |
|---|---|
| `basic_blocks` | Number of basic blocks |
| `cyclomatic` | Cyclomatic complexity, `edges - blocks + 2`, at least `1` |
| `loops` | Number of natural loops, nested ones included |
| `dangerous_calls` | Call sites of memory-unsafe functions from [`dangerous_functions.txt`](../dangerous_functions.txt) |
| `local_vars` | Local variables from debug info, else the number of `alloca` instructions |
| `interesting` | Takes a pointer and lies on a pointer-passing path from an entry |
| `bottleneck` | Every path from an entry to some other reachable function goes through it |
| `dead_end` | Calls nothing that the `interesting` chain continues into |

**`dangerous_calls`.** The list in `dangerous_functions.txt` holds one name
per line, `#` comments, and a trailing `*` for a prefix match. It covers
`memcpy`, `memmove`, `memset` and the `llvm.mem*` intrinsics, `strcpy`,
`strcat` and similar, the `sprintf` and `scanf` families, `gets`, `alloca`,
and the `malloc`, `calloc` and `realloc` allocators. The list is compiled into
the analyzer, so rebuild (`make build`) after editing it.

**`local_vars`.** Counted from `DILocalVariable` entries, without parameters,
when the bitcode has debug info. Otherwise the analyzer counts `alloca`
instructions before it promotes them to registers. Build with `-g` for the
source-level count.

**`interesting`.** The function takes at least one pointer argument and lies on
a call path from an entry along which every function takes a pointer. It marks
code that the fuzz input, a pointer, can plausibly be passed into. The chain
starts at every entry that takes a pointer, and at every pointer-taking closure
or lambda defined inside an entry (Rust `{{closure}}` and `{closure#N}`, C++
`{lambda…}` and clang's `$_N`). That covers ziggy and cargo-afl harnesses,
whose Rust `main` takes no pointer and hands the input to a `fuzz!` closure.

The implicit `this` of a C++ method does not make the method `interesting` by
itself, but the chain continues through it. The analyzer recognizes `this`
from full debug info (`-g`). Without it, it recognizes constructors and
destructors, `const`, `volatile` and ref-qualified methods, functions in an
Itanium vtable, and methods whose mangled signature has no parameters but that
take one pointer. Other methods without full debug info still count `this`, and
so do internal functions in optimized bitcode, where dead-argument elimination
can drop `this`. Rust's `self` is an ordinary parameter and counts.

**`bottleneck`.** The function is the immediate dominator of at least one other
reachable function in the call graph from the entries. Leaving it uncovered
leaves everything below it uncovered, which makes it a good target for seeds or
harness changes.

**`dead_end`.** The function makes no calls, or every function it calls is off
the `interesting` chain (and is not a method the chain passes through by its
`this`). It marks where input flow stops.

Tests: `test_analyzer_core.py::test_function_metrics_counts`,
`::test_local_vars_from_debug_info`, `::test_interesting_pointer_path`,
`::test_interesting_starts_at_closure_inside_entry`,
`::test_interesting_starts_at_clang_lambda_inside_entry`,
`::test_implicit_this_does_not_make_a_method_interesting`,
`::test_bottleneck_dominators` and `::test_dead_end`.

## Limitations

The result is a static over-approximation, not dynamic coverage. These cases
can make it under-report:

- **Code without bitcode.** Precompiled libraries, the Rust standard library
  without `--build-std`, and assembly-only units have no body to analyze.
  Callbacks they call are handled as [escapes](#callbacks-handed-to-external-code),
  but functions they call through other means are not seen. The report lists
  the reachable ones without a body in `external_declarations`; see
  [Allowlist or ignorelist](output.md#allowlist-or-ignorelist).
- **Calls made only from inline asm.** A function that inline asm calls by
  symbol name (`call cb`) has no reference in the IR and stays unreachable.
  Passing the address to inline asm as an operand is fine.
- **Addresses computed with integer arithmetic.** A pointer cast to an integer
  and back is handled. A function whose address is computed from another
  address, for example `(fn)((uintptr_t)&f1 + off)` to reach `f2`, has no
  reference and stays unreachable.
- **Callbacks registered only from unreached code.** A callback that only
  unreached code hands to external code stays unreachable. The report lists it
  in `escaped_from_unreached` and the analyzer prints a note. It usually means
  the harness skips an initialization path that the real program runs.
- **Open issues.** [TODO.md](../TODO.md) lists known gaps that are not fixed
  yet.

The analysis also only classifies functions that exist in the bitcode. A Rust
generic that nothing instantiates is never compiled, so it appears in neither
list. Rust names only join within one mangling scheme and between matching
builds; see [rust.md](rust.md#joining-names-across-builds).

### Method-table codebases

Where dispatch runs through registries of function pointers, provider or
vtable structs, or trait objects everywhere (designs like OpenSSL and assimp),
type matching must assume that every address-taken function of a matching type
can be called. Most of the tree then comes back reachable. In a coverage report
nearly everything uncovered shows as "reachable but not reached" and almost
nothing as unreachable, so the split carries little information.

Two things help:

- **Scope the analysis to one subsystem or one harness.** Analyze the
  subsystem's own bitcode with its own entry (`--artifact` on the library or
  object that contains it, `--entry` on the harness), and read the split inside
  that scope only. A focused harness over one parser gives a focused report;
  see the rustyknife run in [EXAMPLES.md](EXAMPLES.md).
- **Read the confidence counts.** `summary.indirect_only` and
  `summary.low_confidence` measure how much of the reachable set exists only
  because of indirect calls, and the per-function `confidence` grades it. Root
  heuristics add much less: on a tree that looks up names at run time,
  `--no-name-roots` typically changes the reachable set by a few percent.
