"""Verifies the JSON `key` stays stable across opt levels. For legacy Rust
mangling it must strip the trailing `17h<hash>` disambiguator; for v0 mangling
the trailing instantiating crate. The precise strips are proven separately, on
hand-crafted names, by test_json_key_strips_rust_disambiguator and
test_json_key_strips_rust_v0_instantiating_crate in test_analyzer_core.py.
"""

import fnmatch
import os
import re
import shutil

import pytest

from conftest import FIXTURES
from reachability import acquire_rust, analyze, link, toolchain


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


@pytest.mark.skipif(not shutil.which("cargo"), reason="cargo not installed")
def test_rust_key_stable_across_opt(analyzer, tmp_path):
    # Build the same fixture at opt-0 and optimized. Legacy symbols must prove
    # canonicalKey stripped their hash, v0 symbols their instantiating crate.
    # Both schemes must be stable across opt levels.
    tc = toolchain.check_coherence(analyzer)
    _require_rust_readable(tc)

    def work_fns(optimize):
        work = tmp_path / ("opt" if optimize else "noopt")
        shutil.copytree(os.path.join(FIXTURES, "rust_generic"), work,
                        ignore=shutil.ignore_patterns("target"))
        bcs = acquire_rust.acquire_rust_bitcode(str(work), optimize=optimize)
        merged = link.link_bitcode(bcs, str(work / "merged.bc"), tc)
        j = analyze.analyze(merged, tc, ["LLVMFuzzerTestOneInput", "main"])
        return {f["mangled"]: f["key"] for f in j["reachable"]
                if "4work" in f["mangled"]}

    noopt = work_fns(False)
    opt = work_fns(True)
    assert noopt, "no `work` generic instances reached at opt-0"
    disambig = re.compile(r"17h[0-9a-f]{16}E$")
    for fns in (noopt, opt):
        for mangled, key in fns.items():
            if mangled.startswith("_R"):
                assert mangled == key + "B2_", \
                    f"v0 key {key!r} is not {mangled!r} without its " \
                    f"instantiating crate"
                continue
            assert disambig.search(mangled), \
                f"expected a legacy 17h<hash> disambiguator in {mangled}"
            assert mangled.startswith(key) and len(key) < len(mangled), \
                f"key {key!r} is not a strict prefix of mangled {mangled!r}; " \
                f"canonicalKey did not strip the disambiguator"
    assert set(noopt.values()) == set(opt.values())


@pytest.mark.skipif(not shutil.which("cargo"), reason="cargo not installed")
@pytest.mark.parametrize("mangling", ["legacy", "v0"])
def test_upstream_generic_key_survives_instantiating_crate_change(
        analyzer, tmp_path, mangling):
    tc = toolchain.check_coherence(analyzer)
    _require_rust_readable(tc)

    def build(optimize):
        work = tmp_path / f"{mangling}-{'opt' if optimize else 'noopt'}"
        shutil.copytree(os.path.join(FIXTURES, "rust_upstream_generic"), work,
                        ignore=shutil.ignore_patterns("target"))
        try:
            bcs = acquire_rust.acquire_rust_bitcode(
                str(work), optimize=optimize, mangling=mangling)
        except acquire_rust.AcquireError as exc:
            if "only accepted on the nightly compiler" not in str(exc):
                raise
            pytest.skip(f"forcing {mangling} mangling needs a nightly rustc")
        merged = link.link_bitcode(bcs, str(work / "merged.bc"), tc)
        reached = work / "reached.txt"
        j = analyze.analyze(merged, tc, ["LLVMFuzzerTestOneInput"],
                            reached_out=str(reached))
        keys = {f["mangled"]: f["key"] for f in j["reachable"]
                if "8checksum" in f["mangled"]}
        patterns = [line[4:] for line in reached.read_text().splitlines()
                    if line.startswith("fun:") and "8checksum" in line]
        return keys, patterns

    analysis, patterns = build(False)
    fuzz, _ = build(True)
    assert analysis and set(fuzz) - set(analysis), (
        f"expected the optimized build to name checksum::<u8> differently:\n"
        f"  opt-0: {sorted(analysis)}\n  optimized: {sorted(fuzz)}")
    assert set(fuzz.values()) == set(analysis.values())
    for name in fuzz:
        assert any(fnmatch.fnmatchcase(name, p) for p in patterns), \
            f"no opt-0 allowlist entry {patterns} matches {name!r}"
