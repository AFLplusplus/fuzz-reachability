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
    for must in expected["must_reach"]:
        assert any(must in n for n in reachable | demangled), (
            f"{must!r} not reported reachable -- UNSOUND. Reachable: {sorted(reachable)}"
        )
    for forbidden in expected.get("must_not_reach", []):
        assert not any(forbidden in n for n in reachable | demangled), (
            f"{forbidden!r} unexpectedly reachable (over-approximation collapse)"
        )


def _tc(analyzer):
    return toolchain.check_coherence(analyzer)


def _require_rust_readable(tc):
    if not toolchain.rust_bitcode_readable(tc):
        rv = ".".join(str(x) for x in toolchain.rustc_llvm_version())
        cv = ".".join(
            str(x)
            for x in min(
                toolchain.tool_llvm_version(tc.llvm_link),
                toolchain.tool_llvm_version(tc.opt),
            )
        )
        pytest.skip(
            f"toolchain LLVM {cv} is older than rustc's LLVM {rv}; "
            f"cannot read rust bitcode"
        )


def _expected(fixture):
    return json.load(open(os.path.join(FIXTURES, fixture, "expected.json")))


@pytest.mark.parametrize("fixture", ["c_direct", "c_fnptr", "cpp_virtual"])
@pytest.mark.skipif(not HAVE_GLLVM, reason="gllvm not installed")
def test_c_cpp_reachable(analyzer, tmp_path, fixture):
    work = tmp_path / fixture
    shutil.copytree(os.path.join(FIXTURES, fixture), work)
    tc = _tc(analyzer)
    bc = acquire_c.acquire_c_bitcode(str(work), tc, "main.o")
    merged = link.link_bitcode([bc], str(work / "merged.bc"), tc)
    result = analyze.analyze(merged, tc, ["LLVMFuzzerTestOneInput"])
    assert_soundness(result, _expected(fixture))


def _rust_reachable(analyzer, tmp_path, fixture, entries):
    work = tmp_path / fixture
    shutil.copytree(os.path.join(FIXTURES, fixture), work)
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
    bc = acquire_c.acquire_c_bitcode(str(work), tc, "main.o")
    merged = link.link_bitcode([bc], str(work / "merged.bc"), tc)
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
    shutil.copytree(os.path.join(FIXTURES, "mixed_c_rust"), work)
    tc = _tc(analyzer)
    _require_rust_readable(tc)
    glue_bc = acquire_c.acquire_c_bitcode(str(work), tc, "glue.o")
    rust_bcs = acquire_rust.acquire_rust_bitcode(str(work))
    merged = link.link_bitcode([glue_bc, *rust_bcs], str(work / "merged.bc"), tc)
    result = analyze.analyze(merged, tc, ["LLVMFuzzerTestOneInput"])
    assert_soundness(result, _expected("mixed_c_rust"))


@pytest.mark.skipif(not shutil.which("cargo"), reason="cargo not installed")
def test_rust_only_entry_rooting(analyzer, tmp_path):
    # No C++ glue: root directly at the Rust entry symbol.
    result, _ = _rust_reachable(
        analyzer, tmp_path, "mixed_c_rust", ["rust_fuzzer_test_input"]
    )
    names = {f["demangled"] for f in result["reachable"]}
    assert any("parse" in n for n in names)


# --- SVF backend: must satisfy the SAME soundness invariant as type-based ---


@pytest.mark.parametrize("fixture", ["c_direct", "c_fnptr", "cpp_virtual"])
@pytest.mark.skipif(not HAVE_GLLVM, reason="gllvm not installed")
def test_svf_c_cpp_sound(svf_analyzer, tmp_path, fixture):
    work = tmp_path / fixture
    shutil.copytree(os.path.join(FIXTURES, fixture), work)
    tc = _tc(svf_analyzer)
    bc = acquire_c.acquire_c_bitcode(str(work), tc, "main.o")
    merged = link.link_bitcode([bc], str(work / "merged.bc"), tc)
    result = analyze.analyze(merged, tc, ["LLVMFuzzerTestOneInput"], backend="svf")
    assert result["backend"] == "svf"
    assert_soundness(result, _expected(fixture))


@pytest.mark.skipif(not shutil.which("cargo"), reason="cargo not installed")
def test_svf_rust_dyn_sound(svf_analyzer, tmp_path):
    work = tmp_path / "rust_dyn"
    shutil.copytree(os.path.join(FIXTURES, "rust_dyn"), work)
    tc = _tc(svf_analyzer)
    _require_rust_readable(tc)
    bcs = acquire_rust.acquire_rust_bitcode(str(work))
    merged = link.link_bitcode(bcs, str(work / "merged.bc"), tc)
    result = analyze.analyze(merged, tc, ["LLVMFuzzerTestOneInput"], backend="svf")
    assert_soundness(result, _expected("rust_dyn"))
