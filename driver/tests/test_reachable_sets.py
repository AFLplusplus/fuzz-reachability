"""End-to-end soundness tests: build each fixture, analyze, assert the
over-approximation invariant (must_reach subset of reachable, must_not_reach
disjoint from reachable).
"""

import json
import os
import shutil

import pytest

from conftest import FIXTURES
from reachability import acquire_c, acquire_rust, analyze, link, toolchain

HAVE_GLLVM = shutil.which("gclang") is not None


def assert_soundness(result, expected):
    reachable = {f["mangled"] for f in result["reachable"]}
    demangled = {f["demangled"] for f in result["reachable"]}

    def matches(wanted, name):
        base = name.split("(", 1)[0]
        if name == wanted or base == wanted or base.endswith("::" + wanted):
            return True
        if not name.startswith("_ZN"):
            return False
        index = 3
        while index < len(name) and name[index] != "E":
            start = index
            while index < len(name) and name[index].isdigit():
                index += 1
            if start == index:
                return False
            length = int(name[start:index])
            component = name[index:index + length]
            if len(component) != length:
                return False
            if component == wanted:
                return True
            index += length
        return False

    for must in expected["must_reach"]:
        assert any(matches(must, name) for name in reachable | demangled), (
            f"{must!r} not reported reachable -- UNSOUND. Reachable: {sorted(reachable)}"
        )
    for forbidden in expected.get("must_not_reach", []):
        assert not any(matches(forbidden, name) for name in reachable | demangled), (
            f"{forbidden!r} unexpectedly reachable (over-approximation collapse)"
        )


def test_assert_soundness_does_not_use_arbitrary_substrings():
    result = {
        "reachable": [{"mangled": "overrun_helper", "demangled": "ns::overrun_helper()"}]
    }
    with pytest.raises(AssertionError):
        assert_soundness(result, {"must_reach": ["run"]})


def _tc(analyzer):
    return toolchain.check_coherence(analyzer)


def _require_rust_readable(tc):
    if not toolchain.rust_bitcode_readable(tc):
        rv = ".".join(str(x) for x in toolchain.rustc_llvm_version())
        cv = ".".join(
            str(x) for x in toolchain.tool_llvm_version(tc.llvm_link)
        )
        pytest.skip(
            f"toolchain LLVM {cv} is older than rustc's LLVM {rv}; "
            f"cannot read rust bitcode"
        )


def _expected(fixture):
    return json.load(open(os.path.join(FIXTURES, fixture, "expected.json")))


@pytest.mark.parametrize("fixture", ["c_direct", "c_fnptr", "cpp_virtual", "c_codec_table"])
@pytest.mark.skipif(not HAVE_GLLVM, reason="gllvm not installed")
def test_c_cpp_reachable(analyzer, tmp_path, fixture):
    work = tmp_path / fixture
    shutil.copytree(os.path.join(FIXTURES, fixture), work)
    tc = _tc(analyzer)
    bcs = acquire_c.acquire_c_bitcode(str(work), tc, "main.o")
    merged = link.link_bitcode(bcs, str(work / "merged.bc"), tc)
    result = analyze.analyze(merged, tc, ["LLVMFuzzerTestOneInput"])
    assert_soundness(result, _expected(fixture))


def _rust_reachable(analyzer, tmp_path, fixture, entries):
    work = tmp_path / fixture
    shutil.copytree(os.path.join(FIXTURES, fixture), work,
                    ignore=shutil.ignore_patterns("target"))
    tc = _tc(analyzer)
    _require_rust_readable(tc)
    bcs = acquire_rust.acquire_rust_bitcode(str(work))
    merged = link.link_bitcode(bcs, str(work / "merged.bc"), tc)
    return analyze.analyze(merged, tc, entries), tc


@pytest.mark.skipif(not HAVE_GLLVM, reason="gllvm not installed")
def test_c_fnptr_breakdown(analyzer, tmp_path):
    work = tmp_path / "c_fnptr"
    shutil.copytree(os.path.join(FIXTURES, "c_fnptr"), work)
    tc = _tc(analyzer)
    bcs = acquire_c.acquire_c_bitcode(str(work), tc, "main.o")
    merged = link.link_bitcode(bcs, str(work / "merged.bc"), tc)
    result = analyze.analyze(merged, tc, ["LLVMFuzzerTestOneInput"])
    # The fn-pointer handlers are reachable only via the indirect call.
    assert result["summary"]["indirect_only"] >= 1
    assert any(
        f["mangled"] == "truly_dead" for f in result["unreachable_defined"]
    )


@pytest.mark.skipif(not shutil.which("cargo"), reason="cargo not installed")
def test_rust_dyn_reachable(analyzer, tmp_path):
    result, _ = _rust_reachable(analyzer, tmp_path, "rust_dyn", ["LLVMFuzzerTestOneInput"])
    assert_soundness(result, _expected("rust_dyn"))


@pytest.mark.skipif(
    not (HAVE_GLLVM and shutil.which("cargo")), reason="needs gllvm + cargo"
)
def test_mixed_c_rust_reachable(analyzer, tmp_path):
    # Cross-language: C++ glue (gllvm) + Rust entry (rustc emit), merged.
    work = tmp_path / "mixed_c_rust"
    shutil.copytree(os.path.join(FIXTURES, "mixed_c_rust"), work,
                    ignore=shutil.ignore_patterns("target"))
    tc = _tc(analyzer)
    _require_rust_readable(tc)
    glue_bcs = acquire_c.acquire_c_bitcode(str(work), tc, "glue.o")
    rust_bcs = acquire_rust.acquire_rust_bitcode(str(work))
    merged = link.link_bitcode([*glue_bcs, *rust_bcs], str(work / "merged.bc"), tc)
    result = analyze.analyze(merged, tc, ["LLVMFuzzerTestOneInput"])
    assert_soundness(result, _expected("mixed_c_rust"))


@pytest.mark.skipif(not shutil.which("cargo"), reason="cargo not installed")
def test_rust_main_entry(analyzer, tmp_path):
    # ziggy/afl harness shape: a Rust bin rooted at `main`, resolved flexibly
    # (the bare token `main` matches the mangled Rust main -- no symbol needed).
    result, _ = _rust_reachable(analyzer, tmp_path, "rust_main", ["main"])
    assert_soundness(result, _expected("rust_main"))


@pytest.mark.skipif(not shutil.which("cargo"), reason="cargo not installed")
def test_rust_only_entry_rooting(analyzer, tmp_path):
    # No C++ glue: root directly at the Rust entry symbol.
    result, _ = _rust_reachable(
        analyzer, tmp_path, "mixed_c_rust", ["rust_fuzzer_test_input"]
    )
    names = {f["demangled"] for f in result["reachable"]}
    assert any("parse" in n for n in names)


@pytest.mark.skipif(not shutil.which("cargo"), reason="cargo not installed")
def test_workspace_profile_semantics_compile_time(analyzer, tmp_path):
    work = tmp_path / "rust_workspace_profiles"
    shutil.copytree(os.path.join(FIXTURES, "rust_workspace_profiles"), work)
    member = work / "member"
    tc = _tc(analyzer)
    _require_rust_readable(tc)

    debug_bcs = acquire_rust.acquire_rust_bitcode(
        str(member), profile="debug", codegen_units=None,
    )
    debug_merged = link.link_bitcode(debug_bcs, str(tmp_path / "debug.bc"), tc)
    debug = analyze.analyze(debug_merged, tc, ["profile_entry"])
    debug_defined = {
        f["mangled"] for key in ("reachable", "unreachable_defined")
        for f in debug[key]
    }
    assert "assertions_off" in debug_defined
    assert "assertions_on" not in debug_defined

    release_bcs = acquire_rust.acquire_rust_bitcode(
        str(member), profile="release", codegen_units=None,
    )
    release_merged = link.link_bitcode(
        release_bcs, str(tmp_path / "release.bc"), tc,
    )
    release = analyze.analyze(release_merged, tc, ["profile_entry"])
    release_defined = {
        f["mangled"] for key in ("reachable", "unreachable_defined")
        for f in release[key]
    }
    assert "assertions_on" in release_defined
    assert "assertions_off" not in release_defined


@pytest.mark.skipif(not shutil.which("cargo"), reason="cargo not installed")
def test_rust_flag_switch_without_clean_uses_fresh_bitcode(analyzer, tmp_path):
    work = tmp_path / "rust_workspace_profiles"
    shutil.copytree(os.path.join(FIXTURES, "rust_workspace_profiles"), work)
    member = work / "member"
    tc = _tc(analyzer)
    _require_rust_readable(tc)
    legacy = acquire_rust.acquire_rust_bitcode(
        str(member), codegen_units=1, mangling="auto",
    )
    current = acquire_rust.acquire_rust_bitcode(
        str(member), codegen_units=1, mangling="v0",
    )
    assert set(legacy) != set(current)
    merged = link.link_bitcode(current, str(tmp_path / "current.bc"), tc)
    report = analyze.analyze(merged, tc, ["profile_entry"])
    assert report["summary"]["reachable"] >= 2
