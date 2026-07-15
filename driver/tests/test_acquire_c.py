import os
import shutil
import struct

import pytest

from reachability import acquire_c


def _fake_elf(etype, marker=True, executable=False):
    b = bytearray(64)
    b[0:4] = b"\x7fELF"
    b[4] = 2
    b[5] = 1
    struct.pack_into("<H", b, 16, etype)
    data = bytes(b) + (b"\x00.llvm_bc\x00" if marker else b"\x00padpadpad\x00")
    return data


def _write(path, data, executable=False):
    with open(path, "wb") as fh:
        fh.write(data)
    if executable:
        os.chmod(path, 0o755)


def test_build_env_sets_wrappers(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    env = acquire_c._build_env(clang_bindir="/usr/lib/llvm-21/bin")
    assert env["CC"].endswith("gclang")
    assert env["CXX"].endswith("gclang++")
    assert env["LLVM_COMPILER_PATH"] == "/usr/lib/llvm-21/bin"


def test_gllvm_env_pins_exact_tools(tmp_path):
    paths = [
        tmp_path / "opt" / "bin" / "clang-23",
        tmp_path / "different" / "bin" / "clang++-23",
        tmp_path / "link" / "bin" / "llvm-link-23",
    ]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        _write(path, b"", executable=True)
    tc = type("TC", (), {
        "clang": str(paths[0]),
        "clangxx": str(paths[1]),
        "llvm_link": str(paths[2]),
    })()

    env = acquire_c._gllvm_env({}, tc, str(tmp_path / "tools"))
    assert env["LLVM_COMPILER_PATH"] == str(tmp_path / "tools")
    assert env["LLVM_CC_NAME"] == "clang"
    assert env["LLVM_CXX_NAME"] == "clang++"
    assert env["LLVM_LINK_NAME"] == "llvm-link"
    assert env["PATH"].split(os.pathsep)[0] == str(tmp_path / "tools")
    assert shutil.which("llvm-link", path=env["PATH"]) == str(
        tmp_path / "tools" / "llvm-link"
    )
    assert os.readlink(tmp_path / "tools" / "clang") == tc.clang
    assert os.readlink(tmp_path / "tools" / "clang++") == tc.clangxx
    assert os.readlink(tmp_path / "tools" / "llvm-link") == tc.llvm_link


def test_build_env_injects_no_inline_by_default(monkeypatch):
    monkeypatch.delenv("LLVM_BITCODE_GENERATION_FLAGS", raising=False)
    env = acquire_c._build_env("/usr/lib/llvm-21/bin")
    flags = env["LLVM_BITCODE_GENERATION_FLAGS"]
    assert "-fno-inline" in flags
    assert "-fno-inline-functions" in flags


def test_build_env_optimize_omits_injection(monkeypatch):
    monkeypatch.delenv("LLVM_BITCODE_GENERATION_FLAGS", raising=False)
    env = acquire_c._build_env("/usr/lib/llvm-21/bin", optimize=True)
    assert "LLVM_BITCODE_GENERATION_FLAGS" not in env


def test_build_env_preserves_inherited_bcgen_flags(monkeypatch):
    monkeypatch.setenv("LLVM_BITCODE_GENERATION_FLAGS", "-mllvm -x")
    env = acquire_c._build_env("/usr/lib/llvm-21/bin")
    flags = env["LLVM_BITCODE_GENERATION_FLAGS"]
    assert "-mllvm -x" in flags
    assert "-fno-inline" in flags


def test_build_looks_cached():
    assert acquire_c._build_looks_cached("make: Nothing to be done for 'all'.")
    assert acquire_c._build_looks_cached("ninja: no work to do.")
    assert acquire_c._build_looks_cached("make[1]: 'thumbnail' is up to date.")
    assert not acquire_c._build_looks_cached("cc -c foo.c -o foo.o")


def test_detect_build_cmd_none(tmp_path):
    assert acquire_c.detect_build_cmd(str(tmp_path)) is None


def test_detect_build_cmd_make(tmp_path):
    (tmp_path / "Makefile").write_text("all:\n\ttrue\n")
    assert acquire_c.detect_build_cmd(str(tmp_path)) == "make"


def test_detect_build_cmd_cmake(tmp_path):
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n")
    assert acquire_c.detect_build_cmd(str(tmp_path)) == (
        "cmake -S . -B build -DBUILD_SHARED_LIBS=OFF "
        "-DCMAKE_INTERPROCEDURAL_OPTIMIZATION=OFF && cmake --build build"
    )


def test_detect_build_cmd_meson(tmp_path):
    (tmp_path / "meson.build").write_text("project('x', 'c')\n")
    assert acquire_c.detect_build_cmd(str(tmp_path)) == (
        "meson setup build --default-library=static -Db_lto=false && ninja -C build"
    )


def test_detect_build_cmd_ninja(tmp_path):
    (tmp_path / "build.ninja").write_text("rule cc\n")
    assert acquire_c.detect_build_cmd(str(tmp_path)) == "ninja"


def _write_configure(tmp_path, help_text):
    """Drop an executable ./configure that prints `help_text` for any args."""
    script = "#!/bin/sh\ncat <<'EOF'\n" + help_text + "\nEOF\n"
    _write(str(tmp_path / "configure"), script.encode(), executable=True)


def test_detect_build_cmd_configure_precedes_make(tmp_path):
    # A configure that prints no libtool help yields no static flags.
    (tmp_path / "configure").write_text("#!/bin/sh\n")
    (tmp_path / "Makefile").write_text("all:\n\ttrue\n")
    assert acquire_c.detect_build_cmd(str(tmp_path)) == "./configure && make"


def test_detect_build_cmd_configure_static_flags(tmp_path):
    _write_configure(tmp_path,
                     "  --enable-shared[=PKGS]  build shared libraries\n"
                     "  --enable-static[=PKGS]  build static libraries\n")
    assert acquire_c.detect_build_cmd(str(tmp_path)) == (
        "./configure --disable-shared --enable-static && make"
    )


def test_detect_build_cmd_configure_only_static(tmp_path):
    _write_configure(tmp_path, "  --enable-static  build static libraries\n")
    assert acquire_c.detect_build_cmd(str(tmp_path)) == (
        "./configure --enable-static && make"
    )


def test_configure_flags_non_executable_falls_back_to_sh(tmp_path):
    # Not marked executable: exec fails, the `sh configure` fallback runs it.
    (tmp_path / "configure").write_text(
        "#!/bin/sh\necho '  --enable-shared  build shared libraries'\n"
    )
    assert acquire_c._configure_flags(str(tmp_path)) == ["--disable-shared"]


def test_configure_flags_none_when_no_configure(tmp_path):
    assert acquire_c._configure_flags(str(tmp_path)) == []


def test_configure_flags_adds_disable_lto_when_advertised(tmp_path):
    _write_configure(tmp_path,
                     "  --enable-shared[=PKGS]  build shared libraries\n"
                     "  --enable-static[=PKGS]  build static libraries\n"
                     "  --enable-lto            enable link-time optimization\n")
    assert acquire_c.detect_build_cmd(str(tmp_path)) == (
        "./configure --disable-shared --enable-static --disable-lto && make"
    )


def test_detect_build_cmd_autogen_forces_static(tmp_path):
    (tmp_path / "autogen.sh").write_text("#!/bin/sh\n")
    assert acquire_c.detect_build_cmd(str(tmp_path)) == (
        "./autogen.sh && ./configure --disable-shared --enable-static "
        "--disable-lto && make"
    )


def test_detect_build_cmd_configure_ac_forces_static(tmp_path):
    (tmp_path / "configure.ac").write_text("AC_INIT([x],[1])\n")
    assert acquire_c.detect_build_cmd(str(tmp_path)) == (
        "autoreconf -i && ./configure --disable-shared --enable-static "
        "--disable-lto && make"
    )


def test_find_artifacts_prefers_executable_over_object(tmp_path):
    _write(str(tmp_path / "main.o"), _fake_elf(1))
    _write(str(tmp_path / "fuzz"), _fake_elf(2), executable=True)
    found = acquire_c.find_artifacts(str(tmp_path))
    assert os.path.basename(found[0]) == "fuzz"
    assert {os.path.basename(p) for p in found} == {"fuzz", "main.o"}


def test_find_artifacts_prefers_bitcode_marker(tmp_path):
    _write(str(tmp_path / "with_bc"), _fake_elf(2, marker=True), executable=True)
    _write(str(tmp_path / "no_bc"), _fake_elf(2, marker=False), executable=True)
    assert os.path.basename(acquire_c.find_artifacts(str(tmp_path))[0]) == "with_bc"


def test_find_artifacts_ignores_non_binaries(tmp_path):
    (tmp_path / "main.c").write_text("int main(){return 0;}\n")
    (tmp_path / "notes.txt").write_text("hello\n")
    assert acquire_c.find_artifacts(str(tmp_path)) == []


def test_find_artifacts_detects_archive(tmp_path):
    _write(str(tmp_path / "lib.a"), b"!<arch>\n" + b"junk.llvm_bc more")
    found = acquire_c.find_artifacts(str(tmp_path))
    assert [os.path.basename(p) for p in found] == ["lib.a"]


def test_classify_recognizes_fat_macho(tmp_path):
    fat = tmp_path / "fatbin"
    fat.write_bytes(b"\xca\xfe\xba\xbe" + b"\x00" * 16)
    assert acquire_c._classify(str(fat)) == "exec"


def test_member_name_strips_gllvm_naming():
    # gllvm names the per-object bitcode '.<obj>.bc' next to the object.
    assert acquire_c._member_name("/p/libtiff/.tif_aux.o.bc") == "tif_aux.o"
    assert acquire_c._member_name("/p/tools/.thumbnail.o.bc") == "thumbnail.o"
    assert acquire_c._member_name("plain.o.bc") == "plain.o"


def test_bitcode_archives_only_marked(tmp_path):
    _write(str(tmp_path / "withbc.a"), b"!<arch>\n" + b"x.llvm_bc y")
    _write(str(tmp_path / "nobc.a"), b"!<arch>\n" + b"nothing here")
    _write(str(tmp_path / "exec"), _fake_elf(2), executable=True)
    found = acquire_c._bitcode_archives(str(tmp_path))
    assert [os.path.basename(p) for p in found] == ["withbc.a"]


def test_plan_static_libs_auto_picks_linked_archive():
    manifest = ["/p/tools/.thumbnail.o.bc", "/p/lt/.tif_aux.o.bc"]
    members = {
        "/p/lt/libtiff.a": {"tif_aux.o", "tif_getimage.o"},
        "/p/x/libother.a": {"other.o"},
    }
    chosen = acquire_c._plan_static_libs(manifest, members, "auto")
    # libtiff is linked (tif_aux is in the manifest); libother is not.
    assert chosen == ["/p/lt/libtiff.a"]


def test_plan_static_libs_all_includes_everything():
    manifest = ["/p/tools/.thumbnail.o.bc", "/p/lt/.tif_aux.o.bc"]
    members = {
        "/p/lt/libtiff.a": {"tif_aux.o", "tif_getimage.o"},
        "/p/x/libother.a": {"other.o"},
    }
    chosen = acquire_c._plan_static_libs(manifest, members, "all")
    assert set(chosen) == {"/p/lt/libtiff.a", "/p/x/libother.a"}


def test_plan_static_libs_auto_no_manifest_picks_nothing():
    members = {"/p/lt/libtiff.a": {"tif_aux.o"}}
    chosen = acquire_c._plan_static_libs([], members, "auto")
    assert chosen == []


def test_archive_member_names_preserve_spaces(monkeypatch):
    result = type("R", (), {
        "returncode": 0, "stdout": b"member one.o\nplain.o\n",
    })()
    monkeypatch.setattr(acquire_c.shutil, "which", lambda name: "/usr/bin/ar")
    monkeypatch.setattr(acquire_c.subprocess, "run", lambda *a, **k: result)
    assert acquire_c._archive_members("archive.a") == {"member one.o", "plain.o"}


def test_plan_static_libs_all_keeps_distinct_archives():
    manifest = ["/p/tools/.thumbnail.o.bc"]
    members = {
        "/p/port/libport.a": {"dummy.o"},
        "/p/lt/libtiff.a": {"tif_aux.o", "tif_getimage.o", "dummy.o"},
        "/p/lt/libtiffxx.a": {"tif_stream.o", "dummy.o"},
    }
    chosen = acquire_c._plan_static_libs(manifest, members, "all")
    assert set(chosen) == {
        "/p/port/libport.a", "/p/lt/libtiff.a", "/p/lt/libtiffxx.a",
    }


def test_include_static_libs_uses_exact_manifest_paths(monkeypatch):
    archives = ["/p/a/libsame.a", "/p/b/libsame.a"]
    primary = ["/p/app/.main.o.bc", "/p/a/.same.o.bc"]
    manifests = {
        "/p/a/libsame.a.full.bc.llvm.manifest": ["/p/a/.same.o.bc"],
        "/p/b/libsame.a.full.bc.llvm.manifest": ["/p/b/.same.o.bc"],
        "/p/app.bc.llvm.manifest": primary,
    }
    monkeypatch.setattr(acquire_c, "_bitcode_archives", lambda p: archives)
    monkeypatch.setattr(acquire_c, "_archive_members", lambda p: {"same.o"})
    monkeypatch.setattr(acquire_c, "_extract_bc", lambda *a, **k: (True, ""))
    monkeypatch.setattr(
        acquire_c, "_manifest_objects", lambda p: manifests.get(p, []),
    )
    result = acquire_c._include_static_libs(
        "/p", "/p/app", "exec", "/p/app.bc", "auto",
    )
    assert result == ["/p/app/.main.o.bc", "/p/a/libsame.a.full.bc"]


def test_include_static_libs_partial_failure_is_atomic(monkeypatch):
    archives = ["/p/a.a", "/p/b.a"]
    monkeypatch.setattr(acquire_c, "_bitcode_archives", lambda p: archives)
    monkeypatch.setattr(
        acquire_c, "_manifest_objects",
        lambda p: ["/p/.main.o.bc", "/p/.a.o.bc", "/p/.b.o.bc"]
        if p == "/p/app.bc.llvm.manifest" else [p],
    )
    monkeypatch.setattr(
        acquire_c, "_archive_members",
        lambda p: {"a.o"} if p.endswith("a.a") else {"b.o"},
    )
    monkeypatch.setattr(
        acquire_c, "_extract_bc",
        lambda p, *a, **k: (not p.endswith("a.a"), "failed"),
    )
    with pytest.raises(acquire_c.AcquireError, match="refusing to publish") as exc:
        acquire_c._include_static_libs(
            "/p", "/p/app", "exec", "/p/app.bc", "auto",
        )
    assert "successful full extractions retained: b.a" in str(exc.value)


def test_include_static_libs_distinguishes_empty_target_manifest(monkeypatch):
    monkeypatch.setattr(acquire_c, "_bitcode_archives", lambda p: ["/p/lib.a"])
    monkeypatch.setattr(acquire_c, "_manifest_objects", lambda p: [])
    with pytest.raises(acquire_c.AcquireError, match="empty target manifest"):
        acquire_c._include_static_libs(
            "/p", "/p/app", "exec", "/p/app.bc", "auto",
        )


def test_include_static_libs_reports_genuinely_empty_archive(monkeypatch, capsys):
    monkeypatch.setattr(acquire_c, "_bitcode_archives", lambda p: ["/p/empty.a"])
    monkeypatch.setattr(acquire_c, "_archive_members", lambda p: set())
    result = acquire_c._include_static_libs(
        "/p", "/p/app", "archive", "/p/app.bc", "all",
    )
    assert result is None
    assert "static library (genuinely empty): empty.a" in capsys.readouterr().out


def test_run_build_captures_when_not_verbose():
    rc, out = acquire_c._run_build(
        ["sh", "-c", "echo hello; echo oops >&2"], ".", dict(os.environ), False)
    assert rc == 0
    assert "hello" in out and "oops" in out


def test_run_build_no_blank_line_when_one_stream_empty():
    rc, out = acquire_c._run_build(
        ["sh", "-c", "echo only-stdout"], ".", dict(os.environ), False)
    assert rc == 0
    assert out == "only-stdout\n"


def test_run_build_tees_when_verbose(capsys):
    rc, out = acquire_c._run_build(
        ["sh", "-c", "echo streamed"], ".", dict(os.environ), True)
    assert rc == 0
    assert "streamed" in out
    assert "streamed" in capsys.readouterr().out


def test_build_spawn_failure_is_domain_error(monkeypatch):
    def fail(*args, **kwargs):
        raise FileNotFoundError("missing")

    monkeypatch.setattr(acquire_c.subprocess, "Popen", fail)
    with pytest.raises(acquire_c.AcquireError, match="cannot run build command"):
        acquire_c._run_build(["missing-build"], ".", dict(os.environ), False)


def test_get_bc_spawn_failure_is_reported(monkeypatch, tmp_path):
    monkeypatch.setattr(
        acquire_c.subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("missing")),
    )
    ok, detail = acquire_c._extract_bc("artifact", str(tmp_path / "out.bc"))
    assert ok is False
    assert "cannot run get-bc" in detail


class _TC:
    clang = "/usr/bin/clang"


def test_acquire_reports_lto_when_no_artifact(monkeypatch, tmp_path):
    monkeypatch.setattr(acquire_c.shutil, "which", lambda n: "/usr/bin/" + n)
    lto_log = ("WARNING: We are skipping bitcode generation because we are doing "
               "link time optimization, and so the compiler is doing the job for us.")
    monkeypatch.setattr(acquire_c, "_run_build", lambda *a, **k: (0, lto_log))
    monkeypatch.setattr(acquire_c, "find_artifacts", lambda *a, **k: [])
    with pytest.raises(acquire_c.AcquireError) as exc:
        acquire_c.acquire_c_bitcode(str(tmp_path), _TC(), build_cmd=["sh", "-c", "true"])
    msg = str(exc.value)
    assert "Likely cause" in msg
    assert "link-time optimization" in msg


def test_acquire_forwards_optimize_to_build_env(monkeypatch, tmp_path):
    seen = {}

    def fake_build_env(clang_bindir, optimize=False):
        seen["optimize"] = optimize
        return dict(os.environ)

    monkeypatch.setattr(acquire_c.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(acquire_c, "_build_env", fake_build_env)
    monkeypatch.setattr(acquire_c, "_run_build", lambda *a, **k: (0, ""))
    monkeypatch.setattr(acquire_c, "find_artifacts", lambda *a, **k: [])

    with pytest.raises(acquire_c.AcquireError):
        acquire_c.acquire_c_bitcode(
            str(tmp_path), _TC(), build_cmd=["sh", "-c", "true"], optimize=True)
    assert seen["optimize"] is True


def test_missing_explicit_artifact_never_discovers(monkeypatch, tmp_path):
    monkeypatch.setattr(acquire_c.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(acquire_c, "_run_build", lambda *a, **k: (0, ""))
    monkeypatch.setattr(
        acquire_c, "find_artifacts",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("discovery ran")),
    )
    with pytest.raises(acquire_c.AcquireError, match="explicit artifact does not exist"):
        acquire_c.acquire_c_bitcode(
            str(tmp_path), _TC(), artifact="typo", static_libs="none",
            work_dir=str(tmp_path / "work"),
        )


def test_unsupported_explicit_artifact_never_discovers(monkeypatch, tmp_path):
    unsupported = tmp_path / "artifact.txt"
    unsupported.write_text("not an artifact")
    monkeypatch.setattr(acquire_c.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(acquire_c, "_run_build", lambda *a, **k: (0, ""))
    monkeypatch.setattr(
        acquire_c, "find_artifacts",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("discovery ran")),
    )
    with pytest.raises(acquire_c.AcquireError, match=str(unsupported)):
        acquire_c.acquire_c_bitcode(
            str(tmp_path), _TC(), artifact=unsupported.name, static_libs="none",
            work_dir=str(tmp_path / "work"),
        )


def test_omitted_artifact_uses_discovery(monkeypatch, tmp_path):
    artifact = tmp_path / "program"
    _write(str(artifact), _fake_elf(2), executable=True)
    called = []
    monkeypatch.setattr(acquire_c.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(acquire_c, "_run_build", lambda *a, **k: (0, ""))

    def discover(*args, **kwargs):
        called.append(True)
        return [str(artifact)]

    def extract(_artifact, out, **kwargs):
        _write(out, b"bitcode")
        return True, ""

    monkeypatch.setattr(acquire_c, "find_artifacts", discover)
    monkeypatch.setattr(acquire_c, "_extract_bc", extract)
    result = acquire_c.acquire_c_bitcode(
        str(tmp_path), _TC(), static_libs="none",
        work_dir=str(tmp_path / "work"),
    )
    assert called == [True]
    assert len(result) == 1


def test_auto_discovery_rejects_equally_plausible_artifacts(monkeypatch, tmp_path):
    for name in ("first", "second"):
        _write(str(tmp_path / name), _fake_elf(2), executable=True)
    monkeypatch.setattr(acquire_c.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(acquire_c, "_run_build", lambda *a, **k: (0, ""))
    with pytest.raises(acquire_c.AcquireError, match="multiple equally plausible"):
        acquire_c.acquire_c_bitcode(
            str(tmp_path), _TC(), static_libs="none",
            work_dir=str(tmp_path / "work"),
        )
