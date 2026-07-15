"""Verifies the JSON `key` stays stable across opt levels. For legacy Rust
mangling it must strip the trailing `17h<hash>` disambiguator; for v0 mangling
the full mangled name is already the key. The precise distinct-hash legacy
strip is proven separately, on a synthetic hand-crafted disambiguator, by
test_json_key_strips_rust_disambiguator in test_analyzer_core.py.
"""

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
    # canonicalKey stripped their hash; v0 symbols have no legacy hash to strip
    # and must remain intact. Both schemes must be stable across opt levels.
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
                assert key == mangled, \
                    f"v0 key {key!r} differs from mangled name {mangled!r}"
                continue
            assert disambig.search(mangled), \
                f"expected a legacy 17h<hash> disambiguator in {mangled}"
            assert mangled.startswith(key) and len(key) < len(mangled), \
                f"key {key!r} is not a strict prefix of mangled {mangled!r}; " \
                f"canonicalKey did not strip the disambiguator"
    assert set(noopt.values()) == set(opt.values())
