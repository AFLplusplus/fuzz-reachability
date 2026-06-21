from reachability import report


def test_print_summary(capsys):
    result = {
        "backend": "type-based",
        "summary": {"defined": 5, "reachable": 3, "indirect_only": 1, "unreachable": 2},
        "reachable": [
            {"demangled": "foo", "indirect_only": False},
            {"demangled": "bar", "indirect_only": True},
        ],
    }
    report.print_summary(result)
    out = capsys.readouterr().out
    assert "reachable 3 / defined 5" in out
    assert "1 indirect-only" in out
    assert "foo" not in out and "bar" not in out  # no per-function listing
