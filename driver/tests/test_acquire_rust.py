import json
import os

from reachability import acquire_rust


def test_rustflags_contains_emit_bc():
    flags = acquire_rust._rustflags(build_std=False)
    assert "--emit=llvm-bc" in flags
    assert "-Cembed-bitcode=yes" in flags
    assert "-Ccodegen-units=1" in flags


def test_rustflags_build_std():
    assert "-Zbuild-std" in acquire_rust._rustflags(build_std=True)


def test_emit_flags_codegen_units():
    assert "-Ccodegen-units=1" in acquire_rust._emit_flags(False)
    assert "-Ccodegen-units=8" in acquire_rust._emit_flags(False, 8)


def test_base_re_strips_codegen_unit_split():
    assert acquire_rust._BASE_RE.sub(
        "", "cgutest-35522b9e3b1fcb3e.bc") == "cgutest"
    assert acquire_rust._BASE_RE.sub(
        "", "bintest-210615be512f3a47.0hz4fx5p6ud5e1erzexk3zjx4.0e3d7bm.rcgu.bc") == "bintest"


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
    flags = acquire_rust._compose_rustflags(str(tmp_path), build_std=False)
    assert "--emit=llvm-bc" in flags
    assert flags[-2:] == ["--cfg", "tokio_unstable"]


def test_compose_rustflags_keeps_caller_env(tmp_path, monkeypatch):
    monkeypatch.delenv("CARGO_ENCODED_RUSTFLAGS", raising=False)
    monkeypatch.setenv("RUSTFLAGS", "-Cdebuginfo=2")
    flags = acquire_rust._compose_rustflags(str(tmp_path), build_std=False)
    assert "--emit=llvm-bc" in flags
    assert "-Cdebuginfo=2" in flags


def test_build_env_uses_encoded_and_drops_rustflags(tmp_path, monkeypatch):
    monkeypatch.delenv("CARGO_ENCODED_RUSTFLAGS", raising=False)
    monkeypatch.setenv("RUSTFLAGS", "-Cdebuginfo=2")
    env = acquire_rust._build_env(str(tmp_path), build_std=False)
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
