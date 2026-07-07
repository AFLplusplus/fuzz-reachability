import json
import os
import shutil
import types

import pytest

from conftest import FIXTURES
from reachability import cli, toolchain

HAVE_GLLVM = shutil.which("gclang") is not None


class _Ok:
    returncode = 0
    stdout = ""
    stderr = ""


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


def test_native_clean_uses_harness_command(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cli.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(cli.subprocess, "run",
                        lambda argv, **k: calls.append((argv, k.get("cwd"))) or _Ok())
    cli._native_clean(str(tmp_path), "ziggy", verbose=False)
    cli._native_clean(str(tmp_path), "afl", verbose=False)
    assert (["cargo", "ziggy", "clean"], str(tmp_path)) in calls
    assert (["cargo", "afl", "clean"], str(tmp_path)) in calls


def test_native_clean_libfuzzer_targets_fuzz_dir(monkeypatch, tmp_path):
    (tmp_path / "fuzz").mkdir()
    calls = []
    monkeypatch.setattr(cli.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(cli.subprocess, "run",
                        lambda argv, **k: calls.append((argv, k.get("cwd"))) or _Ok())
    cli._native_clean(str(tmp_path), "libfuzzer", verbose=False)
    assert (["cargo", "clean"], str(tmp_path / "fuzz")) in calls


def test_native_clean_skips_when_tool_missing(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cli.shutil, "which", lambda n: None)
    monkeypatch.setattr(cli.subprocess, "run",
                        lambda argv, **k: calls.append(argv) or _Ok())
    cli._native_clean(str(tmp_path), "ziggy", verbose=False)
    assert calls == []


def test_native_clean_skips_when_fuzz_dir_absent(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cli.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(cli.subprocess, "run",
                        lambda argv, **k: calls.append(argv) or _Ok())
    cli._native_clean(str(tmp_path), "libfuzzer", verbose=False)
    assert calls == []


def test_acquire_native_cleans_when_not_optimize(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.acquire_rust, "acquire_rust_bitcode_native",
                        lambda *a, **k: ["x.bc"])
    cleaned = []
    monkeypatch.setattr(cli, "_native_clean",
                        lambda project, lang, verbose: cleaned.append((lang, project)))

    class A:
        lang = "afl"
        project = str(tmp_path)
        build_cmd = None
        profile = None
        optimize = False
        mangling = "auto"
    cli._acquire(A(), tc=None, verbose=False)
    assert cleaned == [("afl", str(tmp_path))]


def test_acquire_native_skips_clean_when_optimize(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.acquire_rust, "acquire_rust_bitcode_native",
                        lambda *a, **k: ["x.bc"])
    cleaned = []
    monkeypatch.setattr(cli, "_native_clean",
                        lambda project, lang, verbose: cleaned.append(lang))

    class A:
        lang = "afl"
        project = str(tmp_path)
        build_cmd = None
        profile = None
        optimize = True
        mangling = "auto"
    cli._acquire(A(), tc=None, verbose=False)
    assert cleaned == []


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


def test_c_run_does_not_require_rust(monkeypatch):
    p = cli.build_parser()
    args = p.parse_args(["run", "--project", "x", "--lang", "c", "--out", "o"])
    seen = {}

    def check(*a, **k):
        seen.update(k)
        return None

    monkeypatch.setattr(cli.toolchain, "check_coherence", check)
    monkeypatch.setattr(cli, "default_analyzer", lambda: "analyzer")
    monkeypatch.setattr(
        cli, "_acquire",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stop")),
    )
    with pytest.raises(RuntimeError):
        cli.cmd_run(args)
    assert seen["require_rust"] is False


def test_check_toolchain_ok(analyzer, monkeypatch):
    monkeypatch.setenv("REACHABILITY_ANALYZER", analyzer)
    assert cli.main(["check-toolchain"]) == 0


def test_run_parser_optimize_defaults_false():
    parser = cli.build_parser()
    args = parser.parse_args(["run", "--lang", "c", "--project", "."])
    assert args.optimize is False


def test_run_parser_optimize_true():
    parser = cli.build_parser()
    args = parser.parse_args(
        ["run", "--lang", "c", "--project", ".", "--optimize"])
    assert args.optimize is True


def _clean_args(project, lang, out):
    p = cli.build_parser()
    return p.parse_args(
        ["run", "--project", str(project), "--lang", lang, "--out", str(out),
         "--clean"]
    )


def test_clean_c_removes_build_artifacts_and_outputs(tmp_path, capsys):
    proj = tmp_path / "cproj"
    (proj / "src").mkdir(parents=True)
    (proj / "build").mkdir()
    (proj / "build" / "nested.o").write_text("x")
    (proj / "merged.bc").write_text("x")
    (proj / "reachability.json").write_text("{}")
    (proj / "reached.txt").write_text("x")
    (proj / "not_reached.txt").write_text("x")
    (proj / "src" / "a.o").write_text("x")
    (proj / "src" / "a.bc").write_text("x")
    (proj / "src" / "a.o.bc").write_text("x")
    (proj / "src" / "a.o.bc.llvm.manifest").write_text("x")
    keep = proj / "src" / "main.c"; keep.write_text("int main(){}")
    git = proj / ".git"; git.mkdir()
    (git / "obj.o").write_text("x")

    out = proj / "reachability.json"
    cli._clean_project(_clean_args(proj, "c", out), verbose=False)

    assert not (proj / "merged.bc").exists()
    assert (proj / "build").exists()
    assert not (proj / "build" / "nested.o").exists()
    assert not (proj / "reachability.json").exists()
    assert not (proj / "reached.txt").exists()
    assert not (proj / "not_reached.txt").exists()
    assert not (proj / "src" / "a.o").exists()
    assert not (proj / "src" / "a.bc").exists()
    assert not (proj / "src" / "a.o.bc").exists()
    assert not (proj / "src" / "a.o.bc.llvm.manifest").exists()
    assert keep.exists()
    assert (git / "obj.o").exists()
    assert "clean: removed" in capsys.readouterr().out


def test_clean_c_runs_build_system_clean(tmp_path, monkeypatch):
    """A configured build tree is cleaned in place with its own tool; the
    directory itself is kept and any leftover objects are still removed."""
    proj = tmp_path / "cproj"
    bdir = proj / "build"; bdir.mkdir(parents=True)
    (bdir / "Makefile").write_text("clean:\n\t:\n")
    (bdir / "obj.o").write_text("x")

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    out = proj / "reachability.json"
    cli._clean_project(_clean_args(proj, "c", out), verbose=False)

    assert ["make", "-C", str(bdir), "clean"] in calls
    assert bdir.exists()
    assert not (bdir / "obj.o").exists()


def test_clean_rust_removes_target_dir(tmp_path, monkeypatch):
    """With no Cargo.toml, _cargo_clean falls back to removing target/ directly,
    so the test is deterministic without invoking cargo."""
    proj = tmp_path / "rproj"
    (proj / "target" / "debug" / "deps").mkdir(parents=True)
    (proj / "target" / "debug" / "deps" / "crate-0123456789abcdef.bc").write_text("x")
    (proj / "merged.bc").write_text("x")
    monkeypatch.setattr(cli.shutil, "which", lambda _name: None)

    out = proj / "reachability.json"
    cli._clean_project(_clean_args(proj, "rust", out), verbose=False)

    assert not (proj / "target").exists()
    assert not (proj / "merged.bc").exists()


def test_run_clean_is_invoked(tmp_path, monkeypatch):
    proj = tmp_path / "p"; proj.mkdir()
    out = proj / "r.json"
    args = _clean_args(proj, "c", out)
    monkeypatch.setattr(cli.toolchain, "check_coherence", lambda *a, **k: None)
    monkeypatch.setattr(cli, "default_analyzer", lambda *a, **k: "analyzer")
    seen = {}
    monkeypatch.setattr(cli, "_clean_project",
                        lambda a, verbose=False: seen.setdefault("clean", True))
    monkeypatch.setattr(
        cli, "_acquire",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stop")),
    )
    with pytest.raises(RuntimeError):
        cli.cmd_run(args)
    assert seen.get("clean") is True


def test_acquire_forwards_optimize_to_rust(monkeypatch, tmp_path):
    seen = {}

    def fake_rust(project_dir, profile="debug", build_std=False,
                 codegen_units=None, verbose=False, optimize=False,
                 mangling="auto"):
        seen["optimize"] = optimize
        return ["x.bc"]

    monkeypatch.setattr(cli.acquire_rust, "acquire_rust_bitcode", fake_rust)

    class A:
        lang = "rust"
        project = str(tmp_path)
        build_cmd = None
        profile = "debug"
        build_std = False
        codegen_units = None
        static_libs = "auto"
        artifact = None
        optimize = True
        mangling = "auto"

    cli._acquire(A(), tc=None, verbose=False)
    assert seen["optimize"] is True


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
