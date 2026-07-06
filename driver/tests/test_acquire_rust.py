import json
import os

import pytest

from reachability import acquire_rust


def test_emit_flags_codegen_units():
    assert "-Ccodegen-units=1" in acquire_rust._emit_flags()
    assert "-Ccodegen-units=8" in acquire_rust._emit_flags(8)


def test_base_re_strips_codegen_unit_split():
    assert acquire_rust._BASE_RE.sub(
        "", "cgutest-35522b9e3b1fcb3e.bc") == "cgutest"
    assert acquire_rust._BASE_RE.sub(
        "", "bintest-210615be512f3a47.0hz4fx5p6ud5e1erzexk3zjx4.0e3d7bm.rcgu.bc") == "bintest"


def test_resolve_codegen_units_explicit_wins(tmp_path):
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "x"\n[profile.release]\ncodegen-units = 1\n')
    assert acquire_rust._resolve_codegen_units(str(tmp_path), "release", 4) == 4


def test_resolve_codegen_units_from_manifest(tmp_path):
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "x"\n[profile.release]\ncodegen-units = 1\n')
    assert acquire_rust._resolve_codegen_units(str(tmp_path), "release", None) == 1


def test_resolve_codegen_units_debug_reads_dev_section(tmp_path):
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "x"\n[profile.dev]\ncodegen-units = 7\n')
    assert acquire_rust._resolve_codegen_units(str(tmp_path), "debug", None) == 7


def test_resolve_codegen_units_cargo_defaults(tmp_path):
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "x"\n')
    assert acquire_rust._resolve_codegen_units(str(tmp_path), "debug", None) == 256
    assert acquire_rust._resolve_codegen_units(str(tmp_path), "release", None) == 16


def test_build_looks_cached_cargo():
    assert acquire_rust._build_looks_cached("    Finished `dev` profile in 0.04s")
    assert not acquire_rust._build_looks_cached(
        "   Compiling foo v0.1.0\n    Finished `dev` profile in 3.0s")
    assert not acquire_rust._build_looks_cached("")


def test_build_looks_cached_make():
    assert acquire_rust._build_looks_cached("make: Nothing to be done for 'all'.")


def test_manifest_codegen_units_walks_up_to_workspace_root(tmp_path):
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["m"]\n[profile.release]\ncodegen-units = 1\n')
    member = tmp_path / "m"
    member.mkdir()
    (member / "Cargo.toml").write_text('[package]\nname = "m"\n')
    assert acquire_rust._manifest_codegen_units(str(member), "release") == 1


def test_config_rustflags_read_array(tmp_path):
    cfg = tmp_path / ".cargo" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[build]\nrustflags = ["--cfg", "tokio_unstable"]\n')
    assert acquire_rust._read_config_rustflags(str(cfg)) == ["--cfg", "tokio_unstable"]


def test_config_rustflags_read_string(tmp_path):
    cfg = tmp_path / ".cargo" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[build]\nrustflags = "--cfg tokio_unstable"\n')
    assert acquire_rust._read_config_rustflags(str(cfg)) == ["--cfg", "tokio_unstable"]


def test_config_rustflags_walks_up_to_parent(tmp_path):
    (tmp_path / ".cargo").mkdir()
    (tmp_path / ".cargo" / "config.toml").write_text(
        '[build]\nrustflags = ["--cfg", "tokio_unstable"]\n')
    child = tmp_path / "fuzz-ziggy"
    child.mkdir()
    assert acquire_rust._config_rustflags(str(child)) == ["--cfg", "tokio_unstable"]


def test_compose_rustflags_merges_project_config(tmp_path, monkeypatch):
    monkeypatch.delenv("RUSTFLAGS", raising=False)
    monkeypatch.delenv("CARGO_ENCODED_RUSTFLAGS", raising=False)
    (tmp_path / ".cargo").mkdir()
    (tmp_path / ".cargo" / "config.toml").write_text(
        '[build]\nrustflags = ["--cfg", "tokio_unstable"]\n')
    flags = acquire_rust._compose_rustflags(str(tmp_path))
    assert "--emit=llvm-bc" in flags
    assert "--cfg" in flags and "tokio_unstable" in flags
    assert flags.index("tokio_unstable") < flags.index("-Copt-level=0")
    assert "-Copt-level=0" in flags


def test_compose_rustflags_keeps_caller_env(tmp_path, monkeypatch):
    monkeypatch.delenv("CARGO_ENCODED_RUSTFLAGS", raising=False)
    monkeypatch.setenv("RUSTFLAGS", "-Cdebuginfo=2")
    flags = acquire_rust._compose_rustflags(str(tmp_path))
    assert "--emit=llvm-bc" in flags
    assert "-Cdebuginfo=2" in flags


def test_build_env_uses_encoded_and_drops_rustflags(tmp_path, monkeypatch):
    monkeypatch.delenv("CARGO_ENCODED_RUSTFLAGS", raising=False)
    monkeypatch.setenv("RUSTFLAGS", "-Cdebuginfo=2")
    env = acquire_rust._build_env(str(tmp_path))
    assert "RUSTFLAGS" not in env
    parts = env["CARGO_ENCODED_RUSTFLAGS"].split("\x1f")
    assert "--emit=llvm-bc" in parts and "-Cdebuginfo=2" in parts


def test_compile_errors_flags_real_failure_not_link():
    link_only = ("   Compiling foo v0.1.0\n"
                 "error: linking with `cc` failed: exit status: 1\n"
                 "error: could not compile `foo` (bin \"foo\") due to 1 previous error\n")
    assert acquire_rust._compile_errors(link_only) == []
    real = "error[E0432]: unresolved import `tokio::unstable`\n"
    assert acquire_rust._compile_errors(real)
    bs = "error: failed to run custom build command for `ring v0.16.0`\n"
    assert acquire_rust._compile_errors(bs)
    assert acquire_rust._compile_errors("error: expected item\n")


def test_cargo_errors_parse_json():
    compile_error = json.dumps({
        "reason": "compiler-message",
        "message": {"level": "error", "message": "expected item"},
    })
    link_error = json.dumps({
        "reason": "compiler-message",
        "message": {"level": "error", "message": "linking with `cc` failed"},
    })
    errors, linked = acquire_rust._cargo_errors(compile_error + "\n" + link_error)
    assert errors == ["expected item"]
    assert linked


def test_rustc_host(monkeypatch):
    class R:
        returncode = 0
        stdout = "rustc 1.92.0-nightly\nhost: aarch64-unknown-linux-gnu\n"

    monkeypatch.setattr(acquire_rust.subprocess, "run", lambda *a, **k: R())
    assert acquire_rust._rustc_host() == "aarch64-unknown-linux-gnu"


def test_failed_cargo_never_uses_stale_bitcode(tmp_path, monkeypatch):
    stale = tmp_path / "target" / "debug" / "deps" / "x-1234567890abcdef.bc"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale")

    class R:
        returncode = 1
        stdout = ""
        stderr = "error: expected item\n"

    monkeypatch.setattr(acquire_rust.subprocess, "run", lambda *a, **k: R())
    with pytest.raises(acquire_rust.AcquireError):
        acquire_rust.acquire_rust_bitcode(str(tmp_path))


def test_build_std_is_only_a_cargo_option(tmp_path, monkeypatch):
    seen = {}

    class R:
        returncode = 0
        stdout = ""
        stderr = ""

    def run(cmd, **kwargs):
        if cmd[:2] == ["rustc", "-vV"]:
            result = R()
            result.stdout = "host: aarch64-unknown-linux-gnu\n"
            return result
        seen["cmd"] = cmd
        seen["env"] = kwargs["env"]
        return R()

    monkeypatch.setattr(acquire_rust.subprocess, "run", run)
    with pytest.raises(acquire_rust.AcquireError):
        acquire_rust.acquire_rust_bitcode(str(tmp_path), build_std=True)
    assert seen["cmd"][-3:] == [
        "-Zbuild-std", "--target", "aarch64-unknown-linux-gnu",
    ]
    assert "-Zbuild-std" not in seen["env"]["CARGO_ENCODED_RUSTFLAGS"]


def test_dedup_newest_per_crate(tmp_path):
    old = tmp_path / "hashbrown-1111111111111111.bc"
    new = tmp_path / "hashbrown-2222222222222222.bc"
    other = tmp_path / "serde-3333333333333333.bc"
    for p in (old, new, other):
        p.write_text("x")
    os.utime(old, (1, 1))
    os.utime(new, (2, 2))
    os.utime(other, (1, 1))
    kept = acquire_rust._dedup_newest_per_crate([str(old), str(new), str(other)])
    assert str(new) in kept and str(other) in kept
    assert str(old) not in kept


def test_dedup_newest_per_crate_keeps_all_cgus(tmp_path):
    new = [tmp_path / f"rust_dyn-2222222222222222.cgu{i}.rcgu.bc" for i in range(3)]
    old = tmp_path / "rust_dyn-1111111111111111.cgu0.rcgu.bc"
    for p in (*new, old):
        p.write_text("x")
    for p in new:
        os.utime(p, (10, 10))
    os.utime(old, (1, 1))
    kept = acquire_rust._dedup_newest_per_crate(
        [str(old)] + [str(p) for p in new])
    assert kept == sorted(str(p) for p in new)


def test_build_bc_paths_from_artifact_stream(tmp_path):
    deps = tmp_path / "target" / "debug" / "deps"
    deps.mkdir(parents=True)
    keep = deps / "msmith-abcdef0123456789.bc"
    stale = deps / "msmith-0000000000000000.bc"
    keep.write_text("x")
    stale.write_text("x")
    line = json.dumps({
        "reason": "compiler-artifact",
        "target": {"name": "msmith", "kind": ["lib"]},
        "filenames": [str(deps / "libmsmith-abcdef0123456789.rlib")],
        "executable": None,
    })
    noise = "Compiling msmith v0.1.0\n" + json.dumps({"reason": "build-finished", "success": True})
    bcs = acquire_rust._build_bc_paths(line + "\n" + noise)
    assert bcs == [str(keep)]


def test_build_bc_paths_includes_bin_crate(tmp_path):
    debug = tmp_path / "target" / "debug"
    deps = debug / "deps"
    deps.mkdir(parents=True)
    lib_bc = deps / "dep_lib-abcdef0123456789.bc"
    bin_bc = deps / "my_fuzz_bin-1111111111111111.bc"
    lib_bc.write_text("x")
    bin_bc.write_text("x")
    lib = json.dumps({
        "reason": "compiler-artifact",
        "target": {"name": "dep_lib", "kind": ["lib"]},
        "filenames": [str(deps / "libdep_lib-abcdef0123456789.rlib")],
        "executable": None,
    })
    binmsg = json.dumps({
        "reason": "compiler-artifact",
        "target": {"name": "my-fuzz-bin", "kind": ["bin"]},
        "filenames": [str(debug / "my-fuzz-bin")],
        "executable": str(debug / "my-fuzz-bin"),
    })
    bcs = acquire_rust._build_bc_paths(lib + "\n" + binmsg)
    assert bcs == sorted([str(lib_bc), str(bin_bc)])


def test_build_bc_paths_includes_staticlib_crate(tmp_path):
    # A staticlib (like cdylib/dylib) reports only the un-hashed uplifted output
    # path, so its bitcode must be resolved by crate name -- including every
    # codegen unit when codegen-units > 1.
    debug = tmp_path / "target" / "debug"
    deps = debug / "deps"
    deps.mkdir(parents=True)
    cgus = [deps / f"rust_dyn-abcdef0123456789.cgu{i}.rcgu.bc" for i in range(3)]
    stale = deps / "rust_dyn-0000000000000000.cgu0.rcgu.bc"
    for p in (*cgus, stale):
        p.write_text("x")
    for p in cgus:
        os.utime(p, (10, 10))
    os.utime(stale, (1, 1))
    msg = json.dumps({
        "reason": "compiler-artifact",
        "target": {"name": "rust_dyn", "kind": ["staticlib"]},
        "filenames": [str(debug / "librust_dyn.a")],
        "executable": None,
    })
    bcs = acquire_rust._build_bc_paths(msg)
    assert bcs == sorted(str(p) for p in cgus)


def test_build_bc_paths_bin_picks_newest(tmp_path):
    debug = tmp_path / "target" / "debug"
    deps = debug / "deps"
    deps.mkdir(parents=True)
    old = deps / "harness-0000000000000000.bc"
    new = deps / "harness-ffffffffffffffff.bc"
    old.write_text("x")
    new.write_text("x")
    os.utime(old, (1, 1))
    os.utime(new, (10, 10))
    binmsg = json.dumps({
        "reason": "compiler-artifact",
        "target": {"name": "harness", "kind": ["bin"]},
        "filenames": [str(debug / "harness")],
        "executable": str(debug / "harness"),
    })
    bcs = acquire_rust._build_bc_paths(binmsg)
    assert bcs == [str(new)]


def test_compose_rustflags_appends_opt0_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("RUSTFLAGS", raising=False)
    monkeypatch.delenv("CARGO_ENCODED_RUSTFLAGS", raising=False)
    flags = acquire_rust._compose_rustflags(str(tmp_path))
    assert "-Copt-level=0" in flags


def test_compose_rustflags_optimize_omits_opt0(tmp_path, monkeypatch):
    monkeypatch.delenv("RUSTFLAGS", raising=False)
    monkeypatch.delenv("CARGO_ENCODED_RUSTFLAGS", raising=False)
    flags = acquire_rust._compose_rustflags(str(tmp_path), optimize=True)
    assert "-Copt-level=0" not in flags


def test_compose_rustflags_opt0_wins_over_inherited(tmp_path, monkeypatch):
    monkeypatch.delenv("CARGO_ENCODED_RUSTFLAGS", raising=False)
    monkeypatch.setenv("RUSTFLAGS", "-Copt-level=2")
    flags = acquire_rust._compose_rustflags(str(tmp_path))
    assert flags.index("-Copt-level=0") > flags.index("-Copt-level=2")


def test_build_env_threads_opt0(tmp_path, monkeypatch):
    monkeypatch.delenv("RUSTFLAGS", raising=False)
    monkeypatch.delenv("CARGO_ENCODED_RUSTFLAGS", raising=False)
    env = acquire_rust._build_env(str(tmp_path))
    assert "-Copt-level=0" in env["CARGO_ENCODED_RUSTFLAGS"].split("\x1f")
    env_opt = acquire_rust._build_env(str(tmp_path), optimize=True)
    assert "-Copt-level=0" not in env_opt["CARGO_ENCODED_RUSTFLAGS"].split("\x1f")


def test_compose_rustflags_pins_release_assertions_off(tmp_path, monkeypatch):
    monkeypatch.delenv("RUSTFLAGS", raising=False)
    monkeypatch.delenv("CARGO_ENCODED_RUSTFLAGS", raising=False)
    flags = acquire_rust._compose_rustflags(str(tmp_path), profile="release")
    assert "-Cdebug-assertions=off" in flags
    assert "-Coverflow-checks=off" in flags


def test_compose_rustflags_pins_debug_assertions_on(tmp_path, monkeypatch):
    monkeypatch.delenv("RUSTFLAGS", raising=False)
    monkeypatch.delenv("CARGO_ENCODED_RUSTFLAGS", raising=False)
    flags = acquire_rust._compose_rustflags(str(tmp_path), profile="debug")
    assert "-Cdebug-assertions=on" in flags
    assert "-Coverflow-checks=on" in flags


def test_compose_rustflags_optimize_omits_assertion_pins(tmp_path, monkeypatch):
    monkeypatch.delenv("RUSTFLAGS", raising=False)
    monkeypatch.delenv("CARGO_ENCODED_RUSTFLAGS", raising=False)
    flags = acquire_rust._compose_rustflags(
        str(tmp_path), optimize=True, profile="release")
    assert not any(f.startswith("-Cdebug-assertions") for f in flags)
    assert not any(f.startswith("-Coverflow-checks") for f in flags)


def test_resolve_assertions_release_defaults_off(tmp_path):
    assert acquire_rust._resolve_assertions(str(tmp_path), "release") == (False, False)


def test_resolve_assertions_debug_defaults_on(tmp_path):
    assert acquire_rust._resolve_assertions(str(tmp_path), "debug") == (True, True)


def test_resolve_assertions_manifest_override(tmp_path):
    (tmp_path / "Cargo.toml").write_text(
        "[package]\nname='x'\nversion='0.0.0'\n"
        "[profile.release]\ndebug-assertions = true\n")
    assert acquire_rust._resolve_assertions(str(tmp_path), "release") == (True, True)


def test_bc_wrapper_appends_extra_rustflags():
    assert "$REACH_EXTRA_RUSTFLAGS" in acquire_rust._BC_WRAPPER


def test_native_sets_opt0_env_by_default(monkeypatch, tmp_path):
    seen = {}

    class R:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, cwd=None, env=None, capture_output=False, text=False):
        seen["extra"] = env.get("REACH_EXTRA_RUSTFLAGS")
        return R()

    monkeypatch.setattr(acquire_rust.subprocess, "run", fake_run)
    monkeypatch.setattr(acquire_rust.glob, "glob", lambda p: ["a.bc"])
    monkeypatch.setattr(acquire_rust.tempfile, "mkdtemp", lambda prefix="": str(tmp_path))
    acquire_rust.acquire_rust_bitcode_native(str(tmp_path), ["cargo", "afl", "build"])
    assert seen["extra"] == "-Copt-level=0"


def test_native_optimize_clears_opt0_env(monkeypatch, tmp_path):
    seen = {}

    class R:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, cwd=None, env=None, capture_output=False, text=False):
        seen["extra"] = env.get("REACH_EXTRA_RUSTFLAGS")
        return R()

    monkeypatch.setattr(acquire_rust.subprocess, "run", fake_run)
    monkeypatch.setattr(acquire_rust.glob, "glob", lambda p: ["a.bc"])
    monkeypatch.setattr(acquire_rust.tempfile, "mkdtemp", lambda prefix="": str(tmp_path))
    acquire_rust.acquire_rust_bitcode_native(
        str(tmp_path), ["cargo", "afl", "build"], optimize=True)
    assert seen["extra"] == ""
