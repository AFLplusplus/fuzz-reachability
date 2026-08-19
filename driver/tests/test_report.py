from reachability import report


def test_print_summary(capsys):
    result = {
        "backend": "type-based",
        "summary": {"defined": 5, "reachable": 3, "indirect_only": 1,
                    "low_confidence": 1, "unreachable": 2},
        "reachable": [
            {"demangled": "foo", "indirect_only": False},
            {"demangled": "bar", "indirect_only": True},
        ],
    }
    report.print_summary(result)
    out = capsys.readouterr().out
    assert "reachable 3 / defined 5" in out
    assert "1 indirect-only" in out
    assert "1 low-confidence" in out
    assert "foo" not in out and "bar" not in out  # no per-function listing


def test_external_advisory_triggers_when_many_external():
    r = {"summary": {"reachable": 4, "external_declarations": 6, "defined": 4,
                     "indirect_only": 0, "low_confidence": 0, "unreachable": 0},
         "backend": "type-based"}
    msg = report.external_advisory(r)
    assert msg and "ignorelist" in msg and "--build-std" in msg


def test_external_advisory_silent_when_few():
    r = {"summary": {"reachable": 100, "external_declarations": 1, "defined": 100,
                     "indirect_only": 0, "low_confidence": 0, "unreachable": 0},
         "backend": "type-based"}
    assert report.external_advisory(r) is None


def _summary(**kw):
    s = {"reachable": 10, "defined": 10, "indirect_only": 0, "low_confidence": 0,
         "unreachable": 0, "external_declarations": 0, "compile_units": 0,
         "optimized_compile_units": 0, "no_inline_definitions": 10}
    s.update(kw)
    return {"summary": s, "backend": "type-based"}


def test_optimization_advisory_fires_on_optimized_debug_info():
    msg = report.optimization_advisory(
        _summary(compile_units=8, optimized_compile_units=8), lang_mode="c")
    assert msg and "8 of 8 compile units" in msg
    assert "--optimize" in msg and "not_reached.txt" in msg


def test_optimization_advisory_silent_on_source_faithful_build():
    assert report.optimization_advisory(
        _summary(compile_units=8, optimized_compile_units=0), lang_mode="c") is None


def test_optimization_advisory_silent_under_optimize_flag():
    assert report.optimization_advisory(
        _summary(compile_units=8, optimized_compile_units=8), lang_mode="c",
        optimize=True) is None


def test_optimization_advisory_no_inline_fallback_without_debug_info():
    msg = report.optimization_advisory(
        _summary(no_inline_definitions=1), lang_mode="cpp")
    assert msg and "1 of 10 definitions" in msg


def test_optimization_advisory_fallback_skips_rust_and_mixed():
    for mode in ("rust", "mixed", None):
        assert report.optimization_advisory(
            _summary(no_inline_definitions=0), lang_mode=mode) is None


def test_optimization_advisory_fallback_silent_when_flags_applied():
    assert report.optimization_advisory(
        _summary(no_inline_definitions=10), lang_mode="c") is None


def test_optimization_advisory_tolerates_older_report():
    assert report.optimization_advisory(
        {"summary": {"reachable": 4, "defined": 4}}, lang_mode="c") is None
