import json
import os
import shutil

import pytest

from conftest import FIXTURES
from reachability import cli, toolchain

HAVE_GLLVM = shutil.which("gclang") is not None


def test_default_analyzer_default_paths(monkeypatch):
    monkeypatch.delenv("REACHABILITY_ANALYZER", raising=False)
    monkeypatch.delenv("REACHABILITY_ANALYZER_SVF", raising=False)
    monkeypatch.setattr(os.path, "isfile", lambda p: True)
    typed = os.path.join("analyzer", "build", "reachability-analyzer")
    svf = os.path.join("analyzer", "build-svf", "reachability-analyzer")
    assert cli.default_analyzer().endswith(typed)
    assert cli.default_analyzer("type-based").endswith(typed)
    assert cli.default_analyzer("svf").endswith(svf)


def test_default_analyzer_env_overrides(monkeypatch, tmp_path):
    typed = tmp_path / "typed"; typed.write_text("")
    svf = tmp_path / "svf"; svf.write_text("")
    monkeypatch.setenv("REACHABILITY_ANALYZER", str(typed))
    monkeypatch.setenv("REACHABILITY_ANALYZER_SVF", str(svf))
    assert cli.default_analyzer() == str(typed)
    assert cli.default_analyzer("type-based") == str(typed)
    assert cli.default_analyzer("svf") == str(svf)


def test_default_analyzer_svf_ignores_type_based_env(monkeypatch, tmp_path):
    typed = tmp_path / "typed"; typed.write_text("")
    monkeypatch.setenv("REACHABILITY_ANALYZER", str(typed))
    monkeypatch.delenv("REACHABILITY_ANALYZER_SVF", raising=False)
    monkeypatch.setattr(os.path, "isfile", lambda p: True)
    assert cli.default_analyzer("svf").endswith(
        os.path.join("analyzer", "build-svf", "reachability-analyzer")
    )


def test_default_analyzer_missing_binary_errors(monkeypatch):
    monkeypatch.setenv("REACHABILITY_ANALYZER", "/no/such/analyzer")
    with pytest.raises(toolchain.ToolchainError):
        cli.default_analyzer()
    monkeypatch.setenv("REACHABILITY_ANALYZER_SVF", "/no/such/svf-analyzer")
    with pytest.raises(toolchain.ToolchainError) as excinfo:
        cli.default_analyzer("svf")
    assert "build-svf" in str(excinfo.value)


def test_target_entry_defaults():
    # source languages and harness target types each imply their default entry.
    assert cli.TARGETS["c"] == ("c", ["main", "LLVMFuzzerTestOneInput"])
    assert cli.TARGETS["cpp"] == ("cpp", ["main", "LLVMFuzzerTestOneInput"])
    assert cli.TARGETS["rust"] == ("rust", ["main"])
    assert cli.TARGETS["ziggy"] == ("rust", ["main"])
    assert cli.TARGETS["afl"] == ("rust", ["main"])
    assert cli.TARGETS["libfuzzer"] == ("rust", ["fuzz_target!"])
    p = cli.build_parser()
    for lang in ("c", "cpp", "rust", "mixed", "ziggy", "afl", "libfuzzer"):
        args = p.parse_args(["run", "--project", "x", "--lang", lang, "--out", "o"])
        assert args.lang == lang


def test_check_toolchain_ok(analyzer, monkeypatch):
    monkeypatch.setenv("REACHABILITY_ANALYZER", analyzer)
    assert cli.main(["check-toolchain"]) == 0


@pytest.mark.skipif(not HAVE_GLLVM, reason="gllvm not installed")
def test_run_c_direct(analyzer, tmp_path, monkeypatch):
    monkeypatch.setenv("REACHABILITY_ANALYZER", analyzer)
    work = tmp_path / "c_direct"
    shutil.copytree(os.path.join(FIXTURES, "c_direct"), work)
    out = tmp_path / "r.json"
    rc = cli.main(["run", "--project", str(work), "--lang", "c", "--out", str(out)])
    assert rc == 0
    result = json.load(open(out))
    reachable = {f["mangled"] for f in result["reachable"]}
    assert {"LLVMFuzzerTestOneInput", "used_a", "used_b"} <= reachable
    assert "dead_fn" not in reachable
