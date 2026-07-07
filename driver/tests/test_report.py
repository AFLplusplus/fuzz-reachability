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
