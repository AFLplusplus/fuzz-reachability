import json
import types

import pytest

from conftest import ll
from reachability import analyze


def _tc(analyzer_path):
    return types.SimpleNamespace(analyzer=analyzer_path)


def test_analyzer_warnings_are_always_forwarded(analyzer, tmp_path, capsys):
    out = tmp_path / "report.json"
    report = analyze.analyze(
        ll("two_funcs.ll"), _tc(analyzer), ["caller", "misspelled"],
        out_path=str(out),
    )
    assert report == json.loads(out.read_text())
    assert "unresolved entry symbols: misspelled" in capsys.readouterr().err


def test_clean_analyzer_run_is_quiet(analyzer, tmp_path, capsys):
    analyze.analyze(
        ll("two_funcs.ll"), _tc(analyzer), ["caller"],
        out_path=str(tmp_path / "report.json"),
    )
    assert capsys.readouterr().err == ""


def test_malformed_analyzer_json_is_domain_error(monkeypatch):
    result = types.SimpleNamespace(returncode=0, stdout=b"{bad", stderr=b"")
    monkeypatch.setattr(analyze.subprocess, "run", lambda *a, **k: result)
    with pytest.raises(analyze.AnalyzeError, match="analyzer JSON"):
        analyze.analyze("input.bc", _tc("analyzer"), ["entry"])


def test_invalid_analyzer_bytes_are_replaced(monkeypatch, capsys):
    result = types.SimpleNamespace(
        returncode=0, stdout=b'{"ok": true}', stderr=b"warning: \xff",
    )
    monkeypatch.setattr(analyze.subprocess, "run", lambda *a, **k: result)
    assert analyze.analyze("input.bc", _tc("analyzer"), ["entry"]) == {"ok": True}
    assert "\ufffd" in capsys.readouterr().err


def test_analyzer_spawn_failure_is_domain_error(monkeypatch):
    def fail(*args, **kwargs):
        raise FileNotFoundError("missing")

    monkeypatch.setattr(analyze.subprocess, "run", fail)
    with pytest.raises(analyze.AnalyzeError, match="cannot run analyzer"):
        analyze.analyze("input.bc", _tc("missing-analyzer"), ["entry"])


def test_analyzer_nonzero_is_bounded_domain_error(monkeypatch):
    result = types.SimpleNamespace(
        returncode=9, stdout=b"", stderr=(b"x" * 200000) + b"failure-tail",
    )
    monkeypatch.setattr(analyze.subprocess, "run", lambda *a, **k: result)
    with pytest.raises(analyze.AnalyzeError) as exc:
        analyze.analyze("input.bc", _tc("analyzer"), ["entry"])
    assert "failure-tail" in str(exc.value)
    assert len(str(exc.value)) < 70000
