import concurrent.futures
import json
import os
import shutil
import types

import pytest

from conftest import FIXTURES
from reachability import cli, outputs, toolchain

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


def test_out_optional_defaults_to_project(tmp_path):
    p = cli.build_parser()
    args = p.parse_args(["run", "--project", str(tmp_path), "--lang", "c"])
    assert args.out is None
    paths = outputs.resolve(args)
    assert paths.json == str(tmp_path / "reachability.json")


def test_out_directory_is_rejected(tmp_path):
    p = cli.build_parser()
    outdir = tmp_path / "results"
    outdir.mkdir()
    args = p.parse_args(
        ["run", "--project", str(tmp_path), "--lang", "c", "--out", str(outdir)]
    )
    with pytest.raises(outputs.OutputError, match="must name a file"):
        outputs.resolve(args)


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
    monkeypatch.setattr(
        toolchain, "rustc_llvm_major",
        lambda: toolchain.analyzer_llvm_major(analyzer),
    )
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


def _output_args(project, values):
    parser = cli.build_parser()
    argv = [
        "run", "--project", str(project), "--lang", "c",
        "--out", str(values["--out"]),
        "--reached", str(values["--reached"]),
        "--not-reached", str(values["--not-reached"]),
        "--dot", str(values["--dot"]), "--clean",
    ]
    return parser.parse_args(argv)


@pytest.mark.parametrize("option", ["--out", "--reached", "--not-reached", "--dot"])
@pytest.mark.parametrize("use_project", [False, True])
def test_clean_rejects_directory_destinations_without_mutation(
        tmp_path, option, use_project):
    project = tmp_path / "project"
    project.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / "sentinel"
    sentinel.write_text("keep")
    valid = {
        "--out": tmp_path / "out.json",
        "--reached": tmp_path / "reached.txt",
        "--not-reached": tmp_path / "not-reached.txt",
        "--dot": tmp_path / "graph.dot",
    }
    valid["--out"].write_text("old")
    valid[option] = project if use_project else external
    args = _output_args(project, valid)
    with pytest.raises(outputs.OutputError, match="must name a file"):
        cli._clean_project(args)
    assert project.is_dir()
    assert external.is_dir()
    assert sentinel.read_text() == "keep"
    if option != "--out":
        assert valid["--out"].read_text() == "old"


@pytest.mark.parametrize("option", ["--out", "--reached", "--not-reached", "--dot"])
@pytest.mark.parametrize("symlink", [False, True])
def test_clean_unlinks_only_selected_files_and_symlinks(tmp_path, option, symlink):
    project = tmp_path / "project"
    project.mkdir()
    values = {
        "--out": tmp_path / "out.json",
        "--reached": tmp_path / "reached.txt",
        "--not-reached": tmp_path / "not-reached.txt",
        "--dot": tmp_path / "graph.dot",
    }
    target = tmp_path / "target"
    target.write_text("sentinel")
    for path in values.values():
        path.write_text("old")
    selected = values[option]
    if symlink:
        selected.unlink()
        selected.symlink_to(target)
    cli._clean_project(_output_args(project, values))
    assert not selected.exists() and not selected.is_symlink()
    assert target.read_text() == "sentinel"


def test_output_collision_lexical_alias(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    values = {
        "--out": tmp_path / "nested" / ".." / "same",
        "--reached": tmp_path / "same",
        "--not-reached": tmp_path / "not-reached",
        "--dot": tmp_path / "dot",
    }
    with pytest.raises(outputs.OutputError, match="collide"):
        outputs.resolve(_output_args(project, values))


def test_output_collision_symlink_alias(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    target = tmp_path / "target"
    target.write_text("old")
    alias = tmp_path / "alias"
    alias.symlink_to(target)
    values = {
        "--out": target,
        "--reached": alias,
        "--not-reached": tmp_path / "not-reached",
        "--dot": tmp_path / "dot",
    }
    with pytest.raises(outputs.OutputError, match="collide"):
        outputs.resolve(_output_args(project, values))


def test_output_transaction_rolls_back_mid_publish(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    values = {
        "--out": tmp_path / "out.json",
        "--reached": tmp_path / "reached.txt",
        "--not-reached": tmp_path / "not-reached.txt",
        "--dot": tmp_path / "graph.dot",
    }
    for path in values.values():
        path.write_text("old")
    paths = outputs.resolve(_output_args(project, values))
    real_replace = outputs.os.replace
    calls = 0

    def failing_replace(source, destination):
        nonlocal calls
        calls += 1
        if calls == 6:
            raise OSError("forced publication failure")
        return real_replace(source, destination)

    with outputs.Transaction(paths) as transaction:
        for option, _ in paths.items():
            with open(transaction.path(option), "w") as fh:
                fh.write("new")
        monkeypatch.setattr(outputs.os, "replace", failing_replace)
        with pytest.raises(outputs.OutputError, match="atomically"):
            transaction.publish()
    assert all(path.read_text() == "old" for path in values.values())


def test_output_staging_io_failure_is_domain_error(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    values = {
        "--out": tmp_path / "out.json",
        "--reached": tmp_path / "reached.txt",
        "--not-reached": tmp_path / "not-reached.txt",
        "--dot": tmp_path / "graph.dot",
    }
    paths = outputs.resolve(_output_args(project, values))
    monkeypatch.setattr(
        outputs.tempfile, "mkstemp",
        lambda *a, **k: (_ for _ in ()).throw(PermissionError("unwritable")),
    )
    with pytest.raises(outputs.OutputError, match="cannot stage output"):
        with outputs.Transaction(paths):
            pass


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
    cache = proj / cli._OWNED_DIR
    cache.mkdir()
    (cache / cli._OWNED_FILE).write_text(json.dumps([
        "src/a.o.bc", "src/a.o.bc.llvm.manifest",
    ]))
    keep = proj / "src" / "main.c"; keep.write_text("int main(){}")
    git = proj / ".git"; git.mkdir()
    (git / "obj.o").write_text("x")

    out = proj / "reachability.json"
    cli._clean_project(_clean_args(proj, "c", out), verbose=False)

    assert not (proj / "merged.bc").exists()
    assert (proj / "build").exists()
    assert (proj / "build" / "nested.o").exists()
    assert not (proj / "reachability.json").exists()
    assert not (proj / "reached.txt").exists()
    assert not (proj / "not_reached.txt").exists()
    assert (proj / "src" / "a.o").exists()
    assert (proj / "src" / "a.bc").exists()
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
    cache = proj / cli._OWNED_DIR
    cache.mkdir()
    (cache / cli._OWNED_FILE).write_text(json.dumps(["build/obj.o"]))

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
                        lambda a, verbose=False, output_paths=None:
                        seen.setdefault("clean", True))
    monkeypatch.setattr(
        cli, "_acquire",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stop")),
    )
    with pytest.raises(RuntimeError):
        cli.cmd_run(args)
    assert seen.get("clean") is True


@pytest.mark.parametrize("marker,expected", [
    ("build.ninja", ["ninja", "-C", "DIRECTORY", "-t", "clean"]),
    ("CMakeCache.txt", ["cmake", "--build", "DIRECTORY", "--target", "clean"]),
])
def test_clean_command_branches(tmp_path, marker, expected):
    (tmp_path / marker).write_text("")
    assert cli._build_clean_cmd(str(tmp_path)) == [
        str(tmp_path) if item == "DIRECTORY" else item for item in expected
    ]


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


def test_expected_cli_error_has_no_traceback(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "default_analyzer",
        lambda: (_ for _ in ()).throw(toolchain.ToolchainError("missing analyzer")),
    )
    assert cli.main(["check-toolchain"]) == 1
    stderr = capsys.readouterr().err
    assert "missing analyzer" in stderr
    assert "Traceback" not in stderr


def test_concurrent_runs_use_isolated_intermediates(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    work_dirs = []
    merged_paths = []
    tc = types.SimpleNamespace(
        llvm_major=23, rustc_major=None, clang="clang", clangxx="clang++",
        llvm_link="llvm-link", analyzer="analyzer",
    )
    monkeypatch.setattr(cli, "default_analyzer", lambda: "analyzer")
    monkeypatch.setattr(cli.toolchain, "check_coherence", lambda *a, **k: tc)

    def acquire(_args, _tc, verbose=False, work_dir=None):
        work_dirs.append(work_dir)
        path = os.path.join(work_dir, "input.bc")
        with open(path, "wb") as fh:
            fh.write(b"bc")
        return [path]

    def link_bitcode(paths, output, _tc):
        assert os.path.dirname(paths[0]) == os.path.dirname(output)
        merged_paths.append(output)
        with open(output, "wb") as fh:
            fh.write(b"merged")
        return output

    def run_analyzer(_merged, _tc, _entries, dot=None, reached_out=None,
                     not_reached_out=None, out_path=None, **kwargs):
        result = {
            "backend": "type-based", "external_declarations": [],
            "summary": {
                "defined": 1, "reachable": 1, "indirect_only": 0,
                "low_confidence": 0, "unreachable": 0,
                "external_declarations": 0,
            },
        }
        with open(out_path, "w") as fh:
            json.dump(result, fh)
        for path in (reached_out, not_reached_out):
            with open(path, "w") as fh:
                fh.write("fun:entry\n")
        return result

    monkeypatch.setattr(cli, "_acquire", acquire)
    monkeypatch.setattr(cli.link, "link_bitcode", link_bitcode)
    monkeypatch.setattr(cli.analyze, "analyze", run_analyzer)
    monkeypatch.setattr(cli.report, "print_summary", lambda result: None)
    monkeypatch.setattr(cli.report, "external_advisory", lambda result: None)

    commands = []
    for index in range(2):
        directory = tmp_path / f"out-{index}"
        directory.mkdir()
        commands.append([
            "run", "--project", str(project), "--lang", "c", "--entry", "entry",
            "--out", str(directory / "report.json"),
        ])
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(cli.main, commands))
    assert results == [0, 0]
    assert len(set(work_dirs)) == 2
    assert len(set(merged_paths)) == 2
    assert all(not os.path.exists(path) for path in work_dirs)
    assert all((tmp_path / f"out-{index}" / "report.json").exists() for index in range(2))


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


@pytest.mark.skipif(not HAVE_GLLVM, reason="gllvm not installed")
def test_run_static_archives_end_to_end(analyzer, tmp_path, monkeypatch):
    monkeypatch.setenv("REACHABILITY_ANALYZER", analyzer)
    work = tmp_path / "c_static_archives"
    shutil.copytree(os.path.join(FIXTURES, "c_static_archives"), work)
    out = tmp_path / "report.json"
    rc = cli.main([
        "run", "--project", str(work), "--lang", "c", "--artifact", "app",
        "--static-libs", "auto", "--out", str(out),
    ])
    assert rc == 0
    result = json.load(open(out))
    reachable = {f["mangled"] for f in result["reachable"]}
    unreachable = {f["mangled"] for f in result["unreachable_defined"]}
    assert {"main", "first_used", "second_used"} <= reachable
    assert {"first_dead", "second_dead"} <= unreachable


@pytest.mark.skipif(not HAVE_GLLVM, reason="gllvm not installed")
def test_static_archive_failure_publishes_no_report(analyzer, tmp_path, monkeypatch):
    monkeypatch.setenv("REACHABILITY_ANALYZER", analyzer)
    work = tmp_path / "c_static_archives"
    shutil.copytree(os.path.join(FIXTURES, "c_static_archives"), work)
    out = tmp_path / "report.json"
    real_extract = cli.acquire_c._extract_bc

    def fail_one(artifact, output, archive=False, **kwargs):
        if archive and os.path.basename(artifact) == "libfirst.a":
            return False, "forced extraction failure"
        return real_extract(artifact, output, archive=archive, **kwargs)

    monkeypatch.setattr(cli.acquire_c, "_extract_bc", fail_one)
    rc = cli.main([
        "run", "--project", str(work), "--lang", "c", "--artifact", "app",
        "--static-libs", "auto", "--out", str(out),
    ])
    assert rc == 1
    assert not out.exists()


def _require_cli_rust(analyzer):
    try:
        selected = toolchain.check_coherence(analyzer, require_rust=True)
    except toolchain.ToolchainError as exc:
        pytest.skip(str(exc))
    if not toolchain.rust_bitcode_readable(selected):
        pytest.skip("selected LLVM cannot read rustc bitcode")


@pytest.mark.skipif(not HAVE_GLLVM, reason="gllvm not installed")
def test_run_cpp_end_to_end(analyzer, tmp_path, monkeypatch):
    monkeypatch.setenv("REACHABILITY_ANALYZER", analyzer)
    work = tmp_path / "cpp_virtual"
    shutil.copytree(os.path.join(FIXTURES, "cpp_virtual"), work)
    out = tmp_path / "cpp.json"
    rc = cli.main([
        "run", "--project", str(work), "--lang", "cpp",
        "--artifact", "main.o", "--out", str(out),
    ])
    assert rc == 0
    names = {f["mangled"] for f in json.load(open(out))["reachable"]}
    assert "LLVMFuzzerTestOneInput" in names


@pytest.mark.parametrize("fixture", ["cpp_complex", "cpp_complex2"])
@pytest.mark.skipif(not HAVE_GLLVM, reason="gllvm not installed")
def test_run_complex_cpp_fixtures(analyzer, tmp_path, monkeypatch, fixture):
    monkeypatch.setenv("REACHABILITY_ANALYZER", analyzer)
    work = tmp_path / fixture
    shutil.copytree(
        os.path.join(FIXTURES, fixture), work,
        ignore=shutil.ignore_patterns("*.o", "*.bc", "reachability.json", "reached.txt", "not_reached.txt"),
    )
    out = tmp_path / f"{fixture}.json"
    assert cli.main([
        "run", "--project", str(work), "--lang", "cpp",
        "--artifact", "main.o", "--out", str(out),
    ]) == 0
    report = json.load(open(out))
    assert report["summary"]["defined"] > 100
    assert (tmp_path / "reached.txt").is_file()
    assert (tmp_path / "not_reached.txt").is_file()


@pytest.mark.skipif(not shutil.which("cargo"), reason="cargo not installed")
def test_run_rust_end_to_end(analyzer, tmp_path, monkeypatch):
    _require_cli_rust(analyzer)
    monkeypatch.setenv("REACHABILITY_ANALYZER", analyzer)
    work = tmp_path / "rust_main"
    shutil.copytree(os.path.join(FIXTURES, "rust_main"), work)
    out = tmp_path / "rust.json"
    assert cli.main([
        "run", "--project", str(work), "--lang", "rust", "--out", str(out),
    ]) == 0
    names = {f["demangled"] for f in json.load(open(out))["reachable"]}
    assert "main" in names and any(
        name == "rust_main::main" or name.startswith("rust_main::main::h")
        for name in names
    )


@pytest.mark.skipif(
    not (HAVE_GLLVM and shutil.which("cargo")), reason="needs gllvm + cargo",
)
def test_run_mixed_end_to_end(analyzer, tmp_path, monkeypatch):
    _require_cli_rust(analyzer)
    monkeypatch.setenv("REACHABILITY_ANALYZER", analyzer)
    work = tmp_path / "mixed_c_rust"
    shutil.copytree(os.path.join(FIXTURES, "mixed_c_rust"), work)
    out = tmp_path / "mixed.json"
    assert cli.main([
        "run", "--project", str(work), "--lang", "mixed",
        "--artifact", "glue.o", "--out", str(out),
    ]) == 0
    names = {f["mangled"] for f in json.load(open(out))["reachable"]}
    assert "LLVMFuzzerTestOneInput" in names


@pytest.mark.skipif(
    not shutil.which("cargo-ziggy"), reason="cargo-ziggy not installed",
)
def test_run_rust_indirect_fixture(analyzer, tmp_path, monkeypatch):
    _require_cli_rust(analyzer)
    monkeypatch.setenv("REACHABILITY_ANALYZER", analyzer)
    work = tmp_path / "rust_indirect"
    shutil.copytree(
        os.path.join(FIXTURES, "rust_indirect"), work,
        ignore=shutil.ignore_patterns("target", "*.bc", "reachability.json", "reached.txt", "not_reached.txt"),
    )
    out = tmp_path / "rust-indirect.json"
    assert cli.main([
        "run", "--project", str(work), "--lang", "ziggy", "--out", str(out),
    ]) == 0
    assert json.load(open(out))["summary"]["reachable"] > 100
