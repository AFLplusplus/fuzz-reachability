import json
import os
import shutil

import pytest

from conftest import FIXTURES
from reachability import cli, toolchain

HAVE_GLLVM = shutil.which("gclang") is not None


def test_default_analyzer_default_path(monkeypatch):
    monkeypatch.delenv("REACHABILITY_ANALYZER", raising=False)
    monkeypatch.setattr(os.path, "isfile", lambda p: True)
    typed = os.path.join("analyzer", "build", "reachability-analyzer")
    assert cli.default_analyzer().endswith(typed)


def test_default_analyzer_env_override(monkeypatch, tmp_path):
    typed = tmp_path / "typed"; typed.write_text("")
    monkeypatch.setenv("REACHABILITY_ANALYZER", str(typed))
    assert cli.default_analyzer() == str(typed)


def test_default_analyzer_missing_binary_errors(monkeypatch):
    monkeypatch.setenv("REACHABILITY_ANALYZER", "/no/such/analyzer")
    with pytest.raises(toolchain.ToolchainError):
        cli.default_analyzer()


def test_native_build_cmd_defaults():
    assert cli._native_build_cmd("afl", None) == ["cargo", "afl", "build"]
    assert cli._native_build_cmd("ziggy", None) == [
        "cargo", "ziggy", "build", "--no-honggfuzz"]
    assert cli._native_build_cmd("libfuzzer", None) == ["cargo", "fuzz", "build"]


def test_native_build_cmd_release():
    assert cli._native_build_cmd("afl", "release")[-1] == "--release"
    assert cli._native_build_cmd("ziggy", "release")[-1] == "--release"
    assert cli._native_build_cmd("libfuzzer", "release")[-1] == "--release"


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


def test_out_optional_defaults_to_project(monkeypatch):
    p = cli.build_parser()
    args = p.parse_args(["run", "--project", "myproj", "--lang", "c"])
    assert args.out is None
    monkeypatch.setattr(cli.toolchain, "check_coherence", lambda *a, **k: None)
    monkeypatch.setattr(cli, "default_analyzer", lambda *a, **k: "analyzer")

    def boom(*a, **k):
        raise RuntimeError("stop after defaulting --out")

    monkeypatch.setattr(cli, "_acquire", boom)
    with pytest.raises(RuntimeError):
        cli.cmd_run(args)
    assert args.out == os.path.join("myproj", "reachability.json")


def test_out_directory_names_json(tmp_path, monkeypatch):
    p = cli.build_parser()
    outdir = tmp_path / "results"
    outdir.mkdir()
    args = p.parse_args(
        ["run", "--project", "myproj", "--lang", "c", "--out", str(outdir)]
    )
    monkeypatch.setattr(cli.toolchain, "check_coherence", lambda *a, **k: None)
    monkeypatch.setattr(cli, "default_analyzer", lambda *a, **k: "analyzer")

    def boom(*a, **k):
        raise RuntimeError("stop after defaulting --out")

    monkeypatch.setattr(cli, "_acquire", boom)
    with pytest.raises(RuntimeError):
        cli.cmd_run(args)
    assert args.out == os.path.join(str(outdir), "reachability.json")


def test_backend_flag_deprecated_warns(monkeypatch, capsys):
    p = cli.build_parser()
    args = p.parse_args(
        ["run", "--project", "x", "--lang", "c", "--out", "o", "--backend", "svf"]
    )
    monkeypatch.setattr(cli.toolchain, "check_coherence", lambda *a, **k: None)
    monkeypatch.setattr(cli, "default_analyzer", lambda *a, **k: "analyzer")

    def boom(*a, **k):
        raise RuntimeError("stop after the deprecation warning")

    monkeypatch.setattr(cli, "_acquire", boom)
    with pytest.raises(RuntimeError):
        cli.cmd_run(args)
    assert "deprecated and ignored" in capsys.readouterr().err


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
