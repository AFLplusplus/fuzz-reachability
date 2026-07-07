"""Verifies the JSON `key` (the mangled name with the Rust `17h<hash>`
disambiguator stripped) stays stable across opt levels and is a proper strip
of the mangled disambiguator, so it -- not the raw mangled name, whose
disambiguator rustc does not guarantee to be stable -- is the sound
cross-build join key the Changelog.md / README.md point at. The precise
distinct-hash strip is proven separately, on a synthetic hand-crafted
disambiguator, by test_json_key_strips_rust_disambiguator in
test_analyzer_core.py.
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
    # Build the same fixture at opt-0 and optimized. The raw 17h<hash> may
    # not actually drift on every rustc, so equal `key` sets alone would not
    # distinguish a real strip from canonicalKey being a no-op; the
    # strict-prefix check below closes that gap by asserting key != mangled
    # for every generic instance, on top of the cross-opt-level equality.
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
            assert disambig.search(mangled), \
                f"expected a legacy 17h<hash> disambiguator in {mangled}"
            assert mangled.startswith(key) and len(key) < len(mangled), \
                f"key {key!r} is not a strict prefix of mangled {mangled!r}; " \
                f"canonicalKey did not strip the disambiguator"
    assert set(noopt.values()) == set(opt.values())
