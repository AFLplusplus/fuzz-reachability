"""Analyzer behavior driven by hand-written .ll golden inputs.

The analyzer parses .ll directly, so these exercise the full pipeline
(load -> graph -> indirect resolve -> reachability -> JSON) without a build
toolchain.
"""

import fnmatch
import json

from conftest import ll

TWO = lambda: ll("two_funcs.ll")
FNPTR = lambda: ll("fnptr.ll")


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
    # --indirect-any links indirect calls to ALL address-taken funcs; but
    # `other` is not address-taken, so it stays unreachable here.
    r = run_analyzer([FNPTR(), "--entry", "entry", "--indirect-any"])
    j = json.loads(r.stdout)
    names = {f["mangled"] for f in j["reachable"]}
    assert {"opt_a", "opt_b"} <= names


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
