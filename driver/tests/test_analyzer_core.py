"""Analyzer behavior driven by hand-written .ll golden inputs.

The analyzer parses .ll directly, so these exercise the full pipeline
(load -> graph -> indirect resolve -> reachability -> JSON) without a build
toolchain.
"""

import fnmatch
import json
import subprocess

import pytest

from conftest import ll

TWO = lambda: ll("two_funcs.ll")
FNPTR = lambda: ll("fnptr.ll")
DEPTH = lambda: ll("depth.ll")
METRICS = lambda: ll("metrics.ll")


def test_load_valid_ll(run_analyzer):
    r = run_analyzer([TWO(), "--entry", "caller"])
    assert r.returncode == 0, r.stderr


def test_load_missing_file_errors(run_analyzer):
    r = run_analyzer(["/nonexistent/x.bc", "--entry", "caller"])
    assert r.returncode != 0


def test_direct_edge_detected(run_analyzer):
    r = run_analyzer([TWO(), "--dump-edges"])
    assert "caller -> callee [direct]" in r.stdout


def test_no_entry_resolved_errors(run_analyzer):
    r = run_analyzer([TWO(), "--entry", "nope"])
    assert r.returncode != 0
    assert "no entry symbol resolved" in r.stderr


def test_json_output(run_analyzer):
    r = run_analyzer([TWO(), "--entry", "caller"])
    assert r.returncode == 0, r.stderr
    j = json.loads(r.stdout)
    names = {f["mangled"] for f in j["reachable"]}
    assert {"caller", "callee"} <= names
    assert j["summary"]["reachable"] == 2
    assert int(j["llvm_version"]) >= 21  # min supported; newer LLVMs allowed
    assert j["backend"] == "type-based"


def test_json_schema_contract(run_analyzer):
    r = run_analyzer([FNPTR(), "--entry", "entry"])
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    assert set(report) == {
        "schema_version", "llvm_version", "backend", "mangling", "entries",
        "summary", "reachable", "unreachable_defined", "external_declarations",
        "escaped_from_unreached", "edges",
    }
    assert report["schema_version"] == 2
    assert set(report["summary"]) == {
        "defined", "reachable", "indirect_only", "low_confidence",
        "unreachable", "external_declarations", "escaped_from_unreached",
        "compile_units", "optimized_compile_units", "no_inline_definitions",
    }
    assert set(report["reachable"][0]) == {
        "mangled", "demangled", "key", "file", "line", "via",
        "indirect_only", "confidence", "depth", "basic_blocks",
        "dangerous_calls", "local_vars", "cyclomatic", "loops", "interesting",
        "bottleneck", "dead_end",
    }
    assert set(report["unreachable_defined"][0]) == {
        "mangled", "demangled", "key", "file", "line",
    }
    assert set(report["edges"][0]) == {"from", "to", "kind"}


def test_json_depth_min_path(run_analyzer):
    r = run_analyzer([DEPTH(), "--entry", "entry"])
    assert r.returncode == 0, r.stderr
    j = json.loads(r.stdout)
    depth = {f["mangled"]: f["depth"] for f in j["reachable"]}
    assert depth["entry"] == 0
    assert depth["mid"] == 1
    assert depth["target"] == 1


def test_json_edges_reachable_only(run_analyzer):
    r = run_analyzer([FNPTR(), "--entry", "entry"])
    assert r.returncode == 0, r.stderr
    j = json.loads(r.stdout)
    edges = {(e["from"], e["to"], e["kind"]) for e in j["edges"]}
    assert ("entry", "opt_a", "indirect") in edges
    assert ("entry", "opt_b", "indirect") in edges
    reachable = {f["mangled"] for f in j["reachable"]}
    for e in j["edges"]:
        assert e["from"] in reachable and e["to"] in reachable
    assert "other" not in {e["to"] for e in j["edges"]}
    assert "take" not in {e["from"] for e in j["edges"]}


def test_function_metrics_counts(run_analyzer):
    r = run_analyzer([METRICS(), "--entry", "harness"])
    assert r.returncode == 0, r.stderr
    m = {f["mangled"]: f for f in json.loads(r.stdout)["reachable"]}
    assert m["harness"]["basic_blocks"] == 3
    assert m["harness"]["dangerous_calls"] == 1
    assert m["harness"]["cyclomatic"] == 2
    assert m["harness"]["loops"] == 0
    assert m["a"]["basic_blocks"] == 3
    assert m["a"]["loops"] == 1
    assert m["a"]["cyclomatic"] == 2
    assert m["c"]["local_vars"] == 2
    assert m["d"]["dangerous_calls"] == 0


def test_local_vars_from_debug_info(run_analyzer):
    r = run_analyzer([ll("metrics_dbg.ll"), "--entry", "process"])
    assert r.returncode == 0, r.stderr
    m = {f["mangled"]: f for f in json.loads(r.stdout)["reachable"]}
    assert m["process"]["local_vars"] == 3
    assert m["process"]["dangerous_calls"] == 1
    assert m["process"]["loops"] == 1


def test_build_optimization_evidence_optimized(run_analyzer):
    r = run_analyzer([ll("optimized_dbg.ll"), "--entry", "entry"])
    assert r.returncode == 0, r.stderr
    s = json.loads(r.stdout)["summary"]
    assert s["compile_units"] == 1
    assert s["optimized_compile_units"] == 1
    assert s["no_inline_definitions"] == 0


def test_build_optimization_evidence_source_faithful(run_analyzer):
    r = run_analyzer([ll("metrics_dbg.ll"), "--entry", "process"])
    assert r.returncode == 0, r.stderr
    s = json.loads(r.stdout)["summary"]
    assert s["compile_units"] == 1
    assert s["optimized_compile_units"] == 0
    assert s["no_inline_definitions"] == s["defined"]


def test_build_optimization_evidence_without_debug_info(run_analyzer):
    r = run_analyzer([TWO(), "--entry", "caller"])
    assert r.returncode == 0, r.stderr
    s = json.loads(r.stdout)["summary"]
    assert s["compile_units"] == 0
    assert s["optimized_compile_units"] == 0


def test_interesting_pointer_path(run_analyzer):
    r = run_analyzer([METRICS(), "--entry", "harness"])
    assert r.returncode == 0, r.stderr
    m = {f["mangled"]: f for f in json.loads(r.stdout)["reachable"]}
    assert m["harness"]["interesting"] is True
    assert m["a"]["interesting"] is True
    assert m["c"]["interesting"] is True
    assert m["b"]["interesting"] is False
    assert m["d"]["interesting"] is False


def test_bottleneck_dominators(run_analyzer):
    r = run_analyzer([METRICS(), "--entry", "harness"])
    assert r.returncode == 0, r.stderr
    m = {f["mangled"]: f for f in json.loads(r.stdout)["reachable"]}
    assert m["harness"]["bottleneck"] is True
    assert m["a"]["bottleneck"] is True
    assert m["b"]["bottleneck"] is True
    assert m["c"]["bottleneck"] is False
    assert m["d"]["bottleneck"] is False


def test_dead_end(run_analyzer):
    r = run_analyzer([METRICS(), "--entry", "harness"])
    assert r.returncode == 0, r.stderr
    m = {f["mangled"]: f for f in json.loads(r.stdout)["reachable"]}
    assert m["harness"]["dead_end"] is False
    assert m["a"]["dead_end"] is False
    assert m["b"]["dead_end"] is True
    assert m["c"]["dead_end"] is True
    assert m["d"]["dead_end"] is True


def test_json_edges_reference_listed_nodes(run_analyzer):
    r = run_analyzer([ll("callback_load.ll"), "--entry", "entry"])
    assert r.returncode == 0, r.stderr
    j = json.loads(r.stdout)
    reachable = {f["mangled"] for f in j["reachable"]}
    for e in j["edges"]:
        assert e["from"] in reachable, f"dangling from: {e['from']}"
        assert e["to"] in reachable, f"dangling to: {e['to']}"


def test_typebased_indirect(run_analyzer):
    r = run_analyzer([FNPTR(), "--entry", "entry"])
    assert r.returncode == 0, r.stderr
    j = json.loads(r.stdout)
    names = {f["mangled"] for f in j["reachable"]}
    assert {"opt_a", "opt_b", "entry"} <= names
    assert "other" not in names
    # opt_a/opt_b reached only via the indirect call -> indirect-only.
    assert j["summary"]["indirect_only"] >= 2
    indirect_only = {f["mangled"] for f in j["reachable"] if f["indirect_only"]}
    assert {"opt_a", "opt_b"} <= indirect_only


def test_indirect_any_includes_other(run_analyzer):
    precise = run_analyzer([FNPTR(), "--entry", "entry"])
    maximal = run_analyzer([FNPTR(), "--entry", "entry", "--indirect-any"])
    assert precise.returncode == 0, precise.stderr
    assert maximal.returncode == 0, maximal.stderr
    precise_names = {f["mangled"] for f in json.loads(precise.stdout)["reachable"]}
    maximal_names = {f["mangled"] for f in json.loads(maximal.stdout)["reachable"]}
    assert "other" not in precise_names
    assert "other" in maximal_names
    assert maximal_names > precise_names


def test_external_callback_loaded_from_local(run_analyzer):
    r = run_analyzer([ll("callback_load.ll"), "--entry", "entry"])
    assert r.returncode == 0, r.stderr
    names = {f["mangled"] for f in json.loads(r.stdout)["reachable"]}
    assert {
        "entry", "wrapper", "target", "global_target", "struct_target",
        "select_target",
    } <= names


def test_backend_flag_deprecated_and_ignored(run_analyzer):
    # --backend is accepted for backward compatibility but warns and is ignored;
    # the type-based backend is always used.
    r = run_analyzer([TWO(), "--entry", "caller", "--backend", "svf"])
    assert r.returncode == 0, r.stderr
    assert "deprecated and ignored" in r.stderr
    j = json.loads(r.stdout)
    assert j["backend"] == "type-based"


def test_missing_entry_suggests_near_miss(run_analyzer):
    # Default entry LLVMFuzzerTestOneInput is absent; suggest the Rust entry.
    r = run_analyzer([ll("rust_entry.ll")])
    assert r.returncode != 0
    assert "no entry symbol resolved" in r.stderr
    assert "rust_fuzzer_test_input" in r.stderr
    assert "did you mean" in r.stderr


def test_rust_entry_rooting(run_analyzer):
    r = run_analyzer([ll("rust_entry.ll"), "--entry", "rust_fuzzer_test_input"])
    j = json.loads(r.stdout)
    names = {f["mangled"] for f in j["reachable"]}
    assert {"rust_fuzzer_test_input", "inner"} <= names


def test_entry_main_resolves_rust_and_c(run_analyzer):
    # `main` matches the C-ABI shim (exact) and the Rust main (demangled ::main).
    r = run_analyzer([ll("entry_resolve.ll"), "--entry", "main"])
    assert r.returncode == 0, r.stderr
    names = {f["mangled"] for f in json.loads(r.stdout)["reachable"]}
    assert {"main", "_ZN4demo4main17h1111111111111111E", "rust_main_leaf"} <= names
    assert "orphan" not in names
    assert "lf_leaf" not in names


def test_entry_demangled_name(run_analyzer):
    # A demangled name roots precisely the Rust main, not the C shim.
    r = run_analyzer([ll("entry_resolve.ll"), "--entry", "demo::main"])
    assert r.returncode == 0, r.stderr
    names = {f["mangled"] for f in json.loads(r.stdout)["reachable"]}
    assert {"_ZN4demo4main17h1111111111111111E", "rust_main_leaf"} <= names
    assert "main" not in names


def test_entry_fuzz_target_alias(run_analyzer):
    # `fuzz_target!` expands to the cargo-fuzz / libFuzzer entries.
    r = run_analyzer([ll("entry_resolve.ll"), "--entry", "fuzz_target!"])
    assert r.returncode == 0, r.stderr
    names = {f["mangled"] for f in json.loads(r.stdout)["reachable"]}
    assert {"LLVMFuzzerTestOneInput", "rust_fuzzer_test_input",
            "lf_leaf", "rf_leaf"} <= names
    assert "orphan" not in names
    assert "_ZN4demo4main17h1111111111111111E" not in names


def test_dlsym_byname_root(run_analyzer):
    # A function reached only via a dlsym-by-name lookup is recovered as a root.
    r = run_analyzer([ll("dlsym_byname.ll"), "--entry", "entry"])
    assert r.returncode == 0, r.stderr
    names = {f["mangled"] for f in json.loads(r.stdout)["reachable"]}
    assert "dyn_target" in names
    # negative controls: exported-but-unnamed and internal-but-named stay out.
    assert "exported_unused" not in names
    assert "internal_named" not in names
    assert "added 1 root" in r.stderr and "dyn_target" in r.stderr


def test_dlsym_byname_disabled(run_analyzer):
    # --no-name-roots turns the heuristic off; the function is unreachable again.
    r = run_analyzer([ll("dlsym_byname.ll"), "--entry", "entry", "--no-name-roots"])
    assert r.returncode == 0, r.stderr
    names = {f["mangled"] for f in json.loads(r.stdout)["reachable"]}
    assert "dyn_target" not in names


def test_name_root_gated_on_dynamic_lookup(run_analyzer):
    # Without a dlsym/dlopen-family call, a matching name string adds no root.
    r = run_analyzer([ll("name_no_dlsym.ll"), "--entry", "entry"])
    assert r.returncode == 0, r.stderr
    names = {f["mangled"] for f in json.loads(r.stdout)["reachable"]}
    assert "dyn_target" not in names


def test_confidence_tiers(run_analyzer):
    # Per-function confidence: high (direct), medium (value-flow evidence the
    # address is callable), low (type match only, no flow evidence).
    r = run_analyzer([ll("confidence.ll"), "--entry", "entry"])
    assert r.returncode == 0, r.stderr
    j = json.loads(r.stdout)
    conf = {f["mangled"]: f["confidence"] for f in j["reachable"]}
    assert conf["entry"] == "high"          # root
    assert conf["direct_leaf"] == "high"    # direct edge
    assert conf["cb_target"] == "medium"    # address escapes to an external fn
    assert conf["real_target"] == "medium"  # address flows via a global to a callee
    assert conf["decoy"] == "low"           # type match only; address sinks into asm
    assert j["summary"]["low_confidence"] == 1
    # decoy is still reachable -- confidence annotates, never prunes.
    assert "decoy" in conf


def test_v0_demangle_selftest(run_analyzer):
    r = run_analyzer(["--selftest-demangle", "_RNvCs1234_4core3foo"])
    assert r.returncode == 0
    assert "core::foo" in r.stdout
    assert "_R" not in r.stdout  # actually demangled, not echoed


def test_coverage_lists(run_analyzer, tmp_path):
    reached = tmp_path / "reached.txt"
    notr = tmp_path / "not_reached.txt"
    r = run_analyzer([FNPTR(), "--entry", "entry",
                      "--reached-out", str(reached), "--not-reached-out", str(notr)])
    assert r.returncode == 0, r.stderr
    rt, nt = reached.read_text(), notr.read_text()
    # allowlist: src:* plus fun: lines for reachable functions.
    assert "src:*" in rt
    assert "fun:opt_a" in rt and "fun:opt_b" in rt and "fun:entry" in rt
    # ignorelist: fun: lines for unreachable functions, and NO src:* (which
    # would otherwise exclude every file).
    assert "src:*" not in nt
    assert "fun:other" in nt and "fun:take" in nt


def test_dot_export(run_analyzer, tmp_path):
    out = tmp_path / "g.dot"
    run_analyzer([FNPTR(), "--entry", "entry", "--dot", str(out)])
    txt = out.read_text()
    assert "digraph" in txt
    assert "dashed" in txt  # indirect edges styled


def test_dot_export_is_deterministic_and_excludes_declarations(run_analyzer, tmp_path):
    first = tmp_path / "first.dot"
    second = tmp_path / "second.dot"
    args = [ll("indirect_external.ll"), "--entry", "entry"]
    a = run_analyzer([*args, "--dot", str(first)])
    b = run_analyzer([*args, "--dot", str(second)])
    assert a.returncode == 0, a.stderr
    assert b.returncode == 0, b.stderr
    assert first.read_bytes() == second.read_bytes()
    assert "external_callback" not in first.read_text()


def _fun_patterns(text):
    return [ln[len("fun:"):] for ln in text.splitlines() if ln.startswith("fun:")]


def test_ignorelist_glob_never_excludes_reachable(run_analyzer, tmp_path):
    reached = tmp_path / "reached.txt"
    notr = tmp_path / "not_reached.txt"
    r = run_analyzer([ll("rust_nested.ll"), "--entry", "LLVMFuzzerTestOneInput",
                      "--reached-out", str(reached), "--not-reached-out", str(notr)])
    assert r.returncode == 0, r.stderr
    reachable = {f["mangled"] for f in json.loads(r.stdout)["reachable"]}
    assert "_ZN3foo3bar4quux17h0123456789abcdefE" in reachable
    ignore = _fun_patterns(notr.read_text())
    offenders = [(p, n) for p in ignore for n in reachable
                 if fnmatch.fnmatchcase(n, p)]
    assert not offenders, (
        f"ignorelist pattern(s) match a REACHABLE function -- using "
        f"not_reached.txt as a sancov/AFL++ ignorelist would exclude reachable "
        f"code from instrumentation: {offenders}"
    )


EXTDECL = lambda: ll("external_decl.ll")


def test_reachable_external_declarations(run_analyzer):
    r = run_analyzer([EXTDECL(), "--entry", "entry"])
    assert r.returncode == 0, r.stderr
    j = json.loads(r.stdout)
    assert j["summary"]["external_declarations"] == 1
    assert j["external_declarations"] == ["ext"]


def test_stores_into_escaping_memory_objects(run_analyzer, tmp_path):
    ignore = tmp_path / "ignore.txt"
    r = run_analyzer([
        ll("escape_stores.ll"), "--entry", "entry",
        "--not-reached-out", str(ignore),
    ])
    assert r.returncode == 0, r.stderr
    expected = {
        "initial_callback", "null_callback", "first_callback",
        "second_callback", "struct_callback", "array_callback",
        "heap_callback", "stack_callback",
    }
    names = {f["mangled"] for f in json.loads(r.stdout)["reachable"]}
    assert expected <= names
    ignored = set(_fun_patterns(ignore.read_text()))
    assert not expected & ignored


def test_alias_chains_resolve_to_direct_callee(run_analyzer):
    r = run_analyzer([ll("alias_chain.ll"), "--entry", "entry"])
    assert r.returncode == 0, r.stderr
    names = {f["mangled"] for f in json.loads(r.stdout)["reachable"]}
    assert {"real", "address_space_real"} <= names
    edges = run_analyzer([ll("alias_chain.ll"), "--dump-edges"])
    assert edges.returncode == 0, edges.stderr
    assert "entry -> real [direct]" in edges.stdout
    assert "entry -> address_space_real [direct]" in edges.stdout
    assert "entry -> external [direct]" in edges.stdout


def test_process_lifecycle_roots_are_opt_in(run_analyzer):
    base = run_analyzer([ll("lifecycle.ll"), "--entry", "entry"])
    assert base.returncode == 0, base.stderr
    base_names = {f["mangled"] for f in json.loads(base.stdout)["reachable"]}
    expected = {
        "constructor", "constructor_leaf", "destructor", "ifunc_resolver",
        "LLVMFuzzerInitialize",
    }
    assert not expected & base_names
    enabled = run_analyzer([
        ll("lifecycle.ll"), "--entry", "entry",
        "--include-process-lifecycle-roots",
    ])
    assert enabled.returncode == 0, enabled.stderr
    report = json.loads(enabled.stdout)
    names = {f["mangled"] for f in report["reachable"]}
    assert expected <= names
    assert expected - {"constructor_leaf"} <= set(report["entries"])
    assert "malformed llvm.global_ctors lifecycle record" in enabled.stderr


def test_type_punned_flow_is_unioned_with_exact_candidates(run_analyzer):
    r = run_analyzer([ll("type_punned.ll"), "--entry", "entry"])
    assert r.returncode == 0, r.stderr
    names = {f["mangled"] for f in json.loads(r.stdout)["reachable"]}
    assert {"punned_target", "exact_decoy"} <= names


def test_exact_indirect_control_stays_narrower_than_indirect_any(run_analyzer):
    exact = run_analyzer([ll("type_punned.ll"), "--entry", "control"])
    maximal = run_analyzer([
        ll("type_punned.ll"), "--entry", "control", "--indirect-any",
    ])
    assert exact.returncode == 0, exact.stderr
    assert maximal.returncode == 0, maximal.stderr
    exact_names = {f["mangled"] for f in json.loads(exact.stdout)["reachable"]}
    maximal_names = {f["mangled"] for f in json.loads(maximal.stdout)["reachable"]}
    assert "punned_target" not in exact_names
    assert "punned_target" in maximal_names


def test_partial_integer_laundering_falls_back_conservatively(run_analyzer):
    r = run_analyzer([
        ll("type_punned.ll"), "--entry", "partially_laundered",
    ])
    assert r.returncode == 0, r.stderr
    names = {f["mangled"] for f in json.loads(r.stdout)["reachable"]}
    assert {"punned_target", "hidden_target"} <= names


def test_resolved_integer_roundtrip_does_not_force_maximal_fallback(run_analyzer):
    r = run_analyzer([
        ll("type_punned.ll"), "--entry", "resolved_integer_roundtrip",
    ])
    assert r.returncode == 0, r.stderr
    names = {f["mangled"] for f in json.loads(r.stdout)["reachable"]}
    assert {"punned_target", "punned_other"} <= names
    assert "hidden_target" not in names


def test_stored_integer_laundering_falls_back_conservatively(run_analyzer):
    r = run_analyzer([
        ll("type_punned.ll"), "--entry", "stored_integer_laundering",
    ])
    assert r.returncode == 0, r.stderr
    names = {f["mangled"] for f in json.loads(r.stdout)["reachable"]}
    assert "hidden_target" in names


@pytest.mark.parametrize("extra", [[], ["--indirect-any"]])
def test_indirect_external_declaration_is_reported(run_analyzer, extra):
    r = run_analyzer([ll("indirect_external.ll"), "--entry", "entry", *extra])
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    assert report["external_declarations"] == ["external_callback"]
    assert report["summary"]["external_declarations"] == 1
    assert report["summary"]["defined"] == 1


def test_confidence_evidence_is_entry_relative(run_analyzer):
    base = run_analyzer([ll("entry_confidence.ll"), "--entry", "entry"])
    assert base.returncode == 0, base.stderr
    confidence = {
        f["mangled"]: f["confidence"] for f in json.loads(base.stdout)["reachable"]
    }
    assert confidence["decoy"] == "low"
    reached = run_analyzer([
        ll("entry_confidence.ll"), "--entry", "entry",
        "--entry", "reached_escape",
    ])
    assert reached.returncode == 0, reached.stderr
    confidence = {
        f["mangled"]: f["confidence"] for f in json.loads(reached.stdout)["reachable"]
    }
    assert confidence["decoy"] == "medium"


def test_operand_bundle_callback_is_reachable(run_analyzer, tmp_path):
    ignored = tmp_path / "ignored.txt"
    r = run_analyzer([
        ll("operand_bundle.ll"), "--entry", "entry",
        "--not-reached-out", str(ignored),
    ])
    assert r.returncode == 0, r.stderr
    names = {f["mangled"] for f in json.loads(r.stdout)["reachable"]}
    assert "callback" in names
    assert "fun:callback" not in ignored.read_text()


def test_defined_personality_is_a_reachable_edge(run_analyzer, tmp_path):
    ignored = tmp_path / "ignored.txt"
    r = run_analyzer([
        ll("personality.ll"), "--entry", "entry",
        "--not-reached-out", str(ignored),
    ])
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    assert "defined_personality" in {f["mangled"] for f in report["reachable"]}
    assert {"from": "entry", "to": "defined_personality", "kind": "direct"} in report["edges"]
    assert "fun:defined_personality" not in ignored.read_text()


def test_invalid_utf8_symbol_is_sanitized(run_analyzer):
    r = run_analyzer([ll("invalid_utf8.ll"), "--entry", "entry"])
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    assert "bad\ufffd" in {f["mangled"] for f in report["reachable"]}


def test_large_call_graph_output_is_deterministic(analyzer, tmp_path):
    path = tmp_path / "large.ll"
    functions = []
    for index in range(1500):
        body = f"  call void @f{index + 1}()\n" if index < 1499 else ""
        functions.append(f"define void @f{index}() {{\n{body}  ret void\n}}\n")
    path.write_text("\n".join(functions))
    first = subprocess.run(
        [analyzer, str(path), "--entry", "f0"], capture_output=True, text=True,
    )
    second = subprocess.run(
        [analyzer, str(path), "--entry", "f0"], capture_output=True, text=True,
    )
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout
    assert json.loads(first.stdout)["summary"]["reachable"] == 1500


RUSTKEY = lambda: ll("rust_key.ll")


def test_json_key_strips_rust_disambiguator(run_analyzer, tmp_path):
    reached = tmp_path / "reached.txt"
    notr = tmp_path / "not_reached.txt"
    r = run_analyzer([RUSTKEY(), "--entry", "entry",
                      "--reached-out", str(reached),
                      "--not-reached-out", str(notr)])
    assert r.returncode == 0, r.stderr
    j = json.loads(r.stdout)
    reach = {f["mangled"]: f for f in j["reachable"]}
    assert reach["_ZN3app4work17h0123456789abcdefE"]["key"] == "_ZN3app4work"
    unreach = {f["mangled"]: f for f in j["unreachable_defined"]}
    assert unreach["_ZN3app4dead17hfedcba9876543210E"]["key"] == "_ZN3app4dead"
    assert "fun:_ZN3app4work*" in reached.read_text()
    assert "fun:_ZN3app4dead*" in notr.read_text()


def test_json_key_strips_rust_v0_instantiating_crate(run_analyzer, tmp_path):
    reached = tmp_path / "reached.txt"
    notr = tmp_path / "not_reached.txt"
    r = run_analyzer([ll("rust_key_v0.ll"), "--entry", "entry",
                      "--reached-out", str(reached),
                      "--not-reached-out", str(notr)])
    assert r.returncode == 0, r.stderr
    j = json.loads(r.stdout)
    key = {f["mangled"]: f["key"] for f in j["reachable"] + j["unreachable_defined"]}
    checksum_u8 = "_RINvCs5zheXYwTW8r_3dep8checksumhE"
    assert key[checksum_u8 + "Cse69bWBSeLps_21rust_upstream_generic"] == checksum_u8
    assert key[checksum_u8 + "B2_"] == checksum_u8
    assert key["_RINvCs5zheXYwTW8r_3dep5scalemEB2_"] == "_RINvCs5zheXYwTW8r_3dep5scalemE"
    for exact in ("_RNvCs5zheXYwTW8r_3dep12dep_checksum",
                  "_RINvCs5zheXYwTW8r_3dep8checksumjEB2_.cold",
                  "_Rprivate_helper"):
        assert key[exact] == exact
    allow = reached.read_text().splitlines()
    assert {f"fun:{checksum_u8}*", "fun:_RINvCs5zheXYwTW8r_3dep5scalemE*",
            "fun:_RNvCs5zheXYwTW8r_3dep12dep_checksum",
            "fun:_Rprivate_helper"} <= set(allow)
    ignore = notr.read_text().splitlines()
    assert "fun:_RINvCs5zheXYwTW8r_3dep8checksumtE*" in ignore
    assert "fun:_RINvCs5zheXYwTW8r_3dep8checksumjEB2_.cold" in ignore
    assert not [line for line in ignore if line.startswith(f"fun:{checksum_u8}")]


def test_run_info_and_edges_toggle(run_analyzer):
    r = run_analyzer([
        FNPTR(), "--entry", "entry", "--no-edges",
        "--run-info", '{"lang": "c", "optimize": false}',
    ])
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    assert report["run"] == {"lang": "c", "optimize": False}
    assert "edges" not in report
    bad = run_analyzer([FNPTR(), "--entry", "entry", "--run-info", "[1]"])
    assert bad.returncode == 1
    assert "--run-info must be a JSON object" in bad.stderr


def test_heap_callback_with_mismatched_type_is_reachable_at_O0(run_analyzer, tmp_path):
    ignored = tmp_path / "ignored.txt"
    r = run_analyzer([
        ll("heap_callback.ll"), "--entry", "LLVMFuzzerTestOneInput",
        "--not-reached-out", str(ignored),
    ])
    assert r.returncode == 0, r.stderr
    names = {f["mangled"] for f in json.loads(r.stdout)["reachable"]}
    assert "free_entry" in names
    assert "fun:free_entry" not in ignored.read_text()


def test_callbacks_handed_out_before_the_first_input(run_analyzer, tmp_path):
    ignored = tmp_path / "ignored.txt"
    r = run_analyzer([
        ll("harness_callbacks.ll"), "--entry", "LLVMFuzzerTestOneInput",
        "--not-reached-out", str(ignored),
    ])
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    names = {f["mangled"] for f in report["reachable"]}
    handed_out = {"on_init_record", "on_ctor_record", "my_hook", "table_hook"}
    assert {"LLVMFuzzerInitialize"} | handed_out <= names
    assert {"ctor", "register_all", "never_handed", "at_exit_only", "static_dtor",
            "pick_callback", "returned_only"}.isdisjoint(names)
    assert {"LLVMFuzzerInitialize", "on_ctor_record"} <= set(report["entries"])
    assert "added LLVMFuzzerInitialize as a harness root" in r.stderr
    assert not {f"fun:{n}" for n in handed_out} & set(ignored.read_text().split())


def test_initialize_is_rooted_only_for_libfuzzer_harnesses(run_analyzer):
    r = run_analyzer([ll("harness_callbacks.ll"), "--entry", "other_entry"])
    assert r.returncode == 0, r.stderr
    names = {f["mangled"] for f in json.loads(r.stdout)["reachable"]}
    assert "LLVMFuzzerInitialize" not in names
    assert "on_init_record" not in names
    assert "on_ctor_record" in names


def test_constructor_bodies_are_rooted_only_on_request(run_analyzer):
    r = run_analyzer([
        ll("harness_callbacks.ll"), "--entry", "LLVMFuzzerTestOneInput",
        "--include-process-lifecycle-roots",
    ])
    assert r.returncode == 0, r.stderr
    names = {f["mangled"] for f in json.loads(r.stdout)["reachable"]}
    assert {"ctor", "register_all", "on_ctor_record", "at_exit_only",
            "static_dtor", "returned_only"} <= names
    assert "callbacks that constructors hand to external code" not in r.stderr


def test_slot_evidence_grades_confidence(run_analyzer):
    r = run_analyzer([ll("slot_confidence.ll"), "--entry", "entry"])
    assert r.returncode == 0, r.stderr
    conf = {f["mangled"]: f["confidence"] for f in json.loads(r.stdout)["reachable"]}
    assert conf["field_called"] == "medium"
    assert conf["field_default"] == "medium"
    assert conf["Impl_run"] == "medium"
    assert conf["field_unused"] == "low"
    assert conf["Impl_other"] == "low"


def test_interesting_starts_at_closure_inside_entry(run_analyzer):
    r = run_analyzer([ll("rust_closure_entry.ll"), "--entry", "zshape::main"])
    assert r.returncode == 0, r.stderr
    m = {f["mangled"]: f for f in json.loads(r.stdout)["reachable"]}
    closure = "_ZN6zshape4main28_$u7b$$u7b$closure$u7d$$u7d$17h1111111111111111E"
    assert m[closure]["interesting"] is True
    assert m["_ZN6zshape5parse17h2222222222222222E"]["interesting"] is True
    assert m["_ZN6zshape4main17h3333333333333333E"]["interesting"] is False
    assert m["_ZN6zshape9fuzz_loop17h0123456789abcdefE"]["interesting"] is False


def test_interesting_starts_at_clang_lambda_inside_entry(run_analyzer):
    r = run_analyzer([ll("cpp_lambda_entry.ll"), "--entry", "main"])
    assert r.returncode == 0, r.stderr
    m = {f["mangled"]: f for f in json.loads(r.stdout)["reachable"]}
    assert m["_ZZ4mainEN3$_08__invokeEPKhm"]["interesting"] is True
    assert m["_ZZ4mainENK3$_0clEPKhm"]["interesting"] is True
    assert m["_ZL5parsePKhm"]["interesting"] is True
    assert m["main"]["interesting"] is False


def test_callbacks_in_memory_owned_by_external_code(run_analyzer, tmp_path):
    ignored = tmp_path / "ignored.txt"
    r = run_analyzer([
        ll("external_hooks.ll"), "--entry", "LLVMFuzzerTestOneInput",
        "--not-reached-out", str(ignored),
    ])
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    names = {f["mangled"] for f in report["reachable"]}
    handed_out = {"my_error", "wrapped_error", "deep_error", "my_cb",
                  "xchg_cb", "cas_cb", "copied_hook"}
    assert handed_out <= names
    assert not {f"fun:{n}" for n in handed_out} & set(ignored.read_text().split())
    assert {"heap_only", "pool_only", "not_cb", "never_published",
            "orphan_cb"}.isdisjoint(names)


def test_external_memory_through_wrappers_helpers_and_out_parameters(run_analyzer):
    r = run_analyzer([ll("external_memory_shapes.ll"), "--entry",
                      "LLVMFuzzerTestOneInput"])
    assert r.returncode == 0, r.stderr
    names = {f["mangled"] for f in json.loads(r.stdout)["reachable"]}
    assert {"phi_cb", "select_cb", "accessor_cb", "helper_cb", "helper_arg_cb",
            "outparam_cb", "wrapped_outparam_cb", "used_outparam_cb",
            "reload_global_cb", "reload_param_cb", "stored_handle_cb",
            "deref_cb"} <= names
    assert {"local_slot_cb", "libc_outparam_cb", "own_memory_cb"}.isdisjoint(names)


def test_callbacks_handed_out_only_by_unreached_code_are_reported(run_analyzer):
    r = run_analyzer([ll("external_hooks.ll"), "--entry", "LLVMFuzzerTestOneInput"])
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    assert report["escaped_from_unreached"] == ["orphan_cb"]
    assert report["summary"]["escaped_from_unreached"] == 1
    assert "only unreached code hands them to external code: orphan_cb" in r.stderr


def test_setters_that_copy_or_choose_a_callback(run_analyzer):
    r = run_analyzer([ll("setter_shapes.ll"), "--entry", "LLVMFuzzerTestOneInput"])
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    names = {f["mangled"] for f in report["reachable"]}
    assert {"via_copy", "via_select", "via_select_b", "via_phi", "via_phi_b",
            "via_memcpy", "via_offset", "via_plain", "via_global",
            "via_init", "via_ctor_arg", "via_template"} <= names
    assert {"never_installed", "copied_never_called",
            "sibling_field"}.isdisjoint(names)


def test_callback_in_object_passed_to_indirect_call(run_analyzer):
    r = run_analyzer([ll("indirect_arg_escape.ll"), "--entry", "LLVMFuzzerTestOneInput"])
    assert r.returncode == 0, r.stderr
    names = {f["mangled"] for f in json.loads(r.stdout)["reachable"]}
    assert "handler" in names
    assert "other_arity" not in names


def test_implicit_this_does_not_make_a_method_interesting(run_analyzer):
    r = run_analyzer([ll("cpp_this_pointer.ll"), "--entry", "LLVMFuzzerTestOneInput"])
    assert r.returncode == 0, r.stderr
    m = {f["mangled"]: f for f in json.loads(r.stdout)["reachable"]}
    for name in ("_ZN6Parser3runEv", "_ZN6Parser4stepEv", "_ZNK6Parser5countEv",
                 "_ZN7Counter4bumpEv", "_ZN6Parser4skipEv"):
        assert m[name]["interesting"] is False, name
    for name in ("_ZN6ParserC2EPKhm", "_ZN6Parser6headerEPKh", "_ZN2ns4peekEPKh",
                 "_ZN12_GLOBAL__N_15Local4feedEPKhm"):
        assert m[name]["interesting"] is True, name
    assert m["_ZN6Parser3runEv"]["dead_end"] is False
    assert m["_ZN6Parser4stepEv"]["dead_end"] is False
    assert m["_ZN6Parser4skipEv"]["dead_end"] is True


def test_vtable_slots_match_only_their_own_dispatch(run_analyzer):
    r = run_analyzer([ll("slot_kinds.ll"), "--entry", "LLVMFuzzerTestOneInput"])
    assert r.returncode == 0, r.stderr
    conf = {f["mangled"]: f["confidence"] for f in json.loads(r.stdout)["reachable"]}
    assert conf["_ZN4Used3runEPv"] == "medium"
    assert conf["_ZN5thing6method17h2222222222222222E"] == "medium"
    assert conf["target"] == "medium"
    assert conf["_ZN4Used5firstEPv"] == "low"
    assert conf["_ZN5Never9unrelatedEPv"] == "low"
    assert conf["_ZN4core3ptr13drop_in_place17h0123456789abcdefE"] == "low"


def test_dump_edges_is_sorted_and_stable(run_analyzer):
    outputs = {run_analyzer([ll("setter_shapes.ll"), "--dump-edges"]).stdout
               for _ in range(5)}
    assert len(outputs) == 1
    lines = outputs.pop().splitlines()
    by_from = {}
    for line in lines:
        src, rest = line.split(" -> ", 1)
        by_from.setdefault(src, []).append(rest)
    for targets in by_from.values():
        assert targets == sorted(targets)


def test_renamed_static_function_keeps_both_lists_safe(run_analyzer, tmp_path):
    reached = tmp_path / "reached.txt"
    notr = tmp_path / "not_reached.txt"
    r = run_analyzer([
        ll("static_collision.ll"), "--entry", "LLVMFuzzerTestOneInput",
        "--reached-out", str(reached), "--not-reached-out", str(notr),
    ])
    assert r.returncode == 0, r.stderr
    allow = set(_fun_patterns(reached.read_text()))
    ignore = set(_fun_patterns(notr.read_text()))
    assert {"helper", "helper.1"} <= allow
    assert "helper" not in ignore
    assert "b_unused" in ignore
