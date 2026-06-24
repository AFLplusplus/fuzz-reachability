import os
import struct

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
        "cmake -S . -B build && cmake --build build"
    )


def test_detect_build_cmd_meson(tmp_path):
    (tmp_path / "meson.build").write_text("project('x', 'c')\n")
    assert acquire_c.detect_build_cmd(str(tmp_path)) == (
        "meson setup build && ninja -C build"
    )


def test_detect_build_cmd_ninja(tmp_path):
    (tmp_path / "build.ninja").write_text("rule cc\n")
    assert acquire_c.detect_build_cmd(str(tmp_path)) == "ninja"


def test_detect_build_cmd_configure_precedes_make(tmp_path):
    (tmp_path / "configure").write_text("#!/bin/sh\n")
    (tmp_path / "Makefile").write_text("all:\n\ttrue\n")
    assert acquire_c.detect_build_cmd(str(tmp_path)) == "./configure && make"


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
    chosen, roots = acquire_c._plan_static_libs(manifest, members, "auto")
    # libtiff is linked (tif_aux is in the manifest); libother is not.
    assert chosen == ["/p/lt/libtiff.a"]
    # only the target's own object is a root; the archive member is not.
    assert roots == ["/p/tools/.thumbnail.o.bc"]


def test_plan_static_libs_all_includes_everything():
    manifest = ["/p/tools/.thumbnail.o.bc", "/p/lt/.tif_aux.o.bc"]
    members = {
        "/p/lt/libtiff.a": {"tif_aux.o", "tif_getimage.o"},
        "/p/x/libother.a": {"other.o"},
    }
    chosen, roots = acquire_c._plan_static_libs(manifest, members, "all")
    assert set(chosen) == {"/p/lt/libtiff.a", "/p/x/libother.a"}
    assert roots == ["/p/tools/.thumbnail.o.bc"]


def test_plan_static_libs_auto_no_manifest_picks_nothing():
    members = {"/p/lt/libtiff.a": {"tif_aux.o"}}
    chosen, roots = acquire_c._plan_static_libs([], members, "auto")
    assert chosen == []
    assert roots == []


def test_plan_static_libs_all_skips_subset_archive():
    manifest = ["/p/tools/.thumbnail.o.bc"]
    members = {
        "/p/port/libport.a": {"dummy.o"},
        "/p/lt/libtiff.a": {"tif_aux.o", "tif_getimage.o", "dummy.o"},
        "/p/lt/libtiffxx.a": {"tif_stream.o", "dummy.o"},
    }
    chosen, roots = acquire_c._plan_static_libs(manifest, members, "all")
    assert set(chosen) == {"/p/lt/libtiff.a", "/p/lt/libtiffxx.a"}
    assert "/p/port/libport.a" not in chosen
    assert roots == ["/p/tools/.thumbnail.o.bc"]
