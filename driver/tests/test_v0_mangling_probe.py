"""Measurement spike (v0 Rust mangling support, Task 1): records what this
toolchain actually does under `-Csymbol-mangling-version=v0` and under
`-Cinstrument-coverage` (which implies v0). These are not correctness tests
of analyzer/driver code -- no product code changed for this task -- they are
informational probes whose findings gate Tasks 2/3 (see
.superpowers/sdd/v0-task-1-report.md). Kept as real assertions (they hold on
this toolchain) rather than xfail, but skipped outright when the toolchain
cannot support the probe, consistent with test_rust_hash_stability.py's
pattern for the same fixture.
"""

import os
import shutil

import pytest

from conftest import FIXTURES
from reachability import acquire_rust, analyze, link, toolchain

pytestmark = pytest.mark.skipif(not shutil.which("cargo"), reason="cargo not installed")


def _require_rust_readable(tc):
    if not toolchain.rust_bitcode_readable(tc):
        rv = ".".join(str(x) for x in toolchain.rustc_llvm_version())
        cv = ".".join(str(x) for x in toolchain.tool_llvm_version(tc.llvm_link))
        pytest.skip(
            f"toolchain LLVM {cv} is older than rustc's LLVM {rv}; "
            f"cannot read rust bitcode"
        )


def _work_symbols(tmp_path, tc, name, monkeypatch, rustflags, optimize):
    """Build a fresh copy of fixtures/rust_generic under `name` with RUSTFLAGS
    set to `rustflags` (mirrors how the driver merges the caller's RUSTFLAGS;
    see acquire_rust._compose_rustflags), and return the set of mangled
    `work` symbols the analyzer reports reachable."""
    work = tmp_path / name
    shutil.copytree(os.path.join(FIXTURES, "rust_generic"), work,
                    ignore=shutil.ignore_patterns("target"))
    monkeypatch.setenv("RUSTFLAGS", rustflags)
    bcs = acquire_rust.acquire_rust_bitcode(str(work), optimize=optimize)
    monkeypatch.delenv("RUSTFLAGS", raising=False)
    merged = link.link_bitcode(bcs, str(work / "merged.bc"), tc)
    j = analyze.analyze(merged, tc, ["LLVMFuzzerTestOneInput"])
    return {f["mangled"] for f in j["reachable"] if "4work" in f["mangled"]}


def test_v0_flag_produces_v0_symbols(analyzer, tmp_path, monkeypatch):
    """Step 1: -Csymbol-mangling-version=v0 is a stable -C flag on this
    nightly (confirmed separately via `rustc -C help`, no -Zunstable-options
    needed) and actually switches codegen to the `_R...` v0 scheme."""
    tc = toolchain.check_coherence(analyzer)
    _require_rust_readable(tc)
    v0 = _work_symbols(tmp_path, tc, "v0_opt0", monkeypatch,
                       "-Csymbol-mangling-version=v0", optimize=False)
    assert v0, "no `work` generic instances reached at v0/opt-0"
    for sym in v0:
        assert sym.startswith("_RINv"), \
            f"expected v0-mangled generic instance ('_RINv...'), got {sym!r}"
        assert "17h" not in sym, \
            f"legacy disambiguator found in what should be a v0 symbol: {sym!r}"


def test_instrument_coverage_matches_explicit_v0(analyzer, tmp_path, monkeypatch):
    """(a) -Cinstrument-coverage implies v0 (per `rustc -C help`) and, at the
    same opt level, produces BYTE-IDENTICAL `work` symbols to explicit
    -Csymbol-mangling-version=v0. If this holds, scheme-matching alone (Task
    2) closes F2 for the coverage case and a v0 key normalizer (Task 3) is
    not required to reconcile analysis vs. coverage builds."""
    tc = toolchain.check_coherence(analyzer)
    _require_rust_readable(tc)
    explicit_v0 = _work_symbols(tmp_path, tc, "explicit_v0", monkeypatch,
                                "-Csymbol-mangling-version=v0", optimize=False)
    coverage = _work_symbols(tmp_path, tc, "coverage", monkeypatch,
                             "-Cinstrument-coverage", optimize=False)
    assert explicit_v0 and coverage
    assert explicit_v0 == coverage, (
        f"v0 symbols differ between explicit v0 and -Cinstrument-coverage:\n"
        f"  explicit v0: {sorted(explicit_v0)}\n"
        f"  coverage:    {sorted(coverage)}"
    )


def test_v0_stable_across_opt_levels(analyzer, tmp_path, monkeypatch):
    """(b) v0 `work` symbols do not drift between opt-0 (analysis default)
    and opt-3 (fixtures/rust_generic's [profile.dev] sets opt-level=3, so
    optimize=True here builds at opt-3 without an explicit -Copt-level flag).
    A locally-defined generic's crate-root Cs<base62>_ disambiguator is
    derived from the crate's stable identity, not from codegen content, so no
    drift is expected here."""
    tc = toolchain.check_coherence(analyzer)
    _require_rust_readable(tc)
    opt0 = _work_symbols(tmp_path, tc, "v0_opt0b", monkeypatch,
                         "-Csymbol-mangling-version=v0", optimize=False)
    opt3 = _work_symbols(tmp_path, tc, "v0_opt3", monkeypatch,
                         "-Csymbol-mangling-version=v0", optimize=True)
    assert opt0 and opt3
    assert opt0 == opt3, (
        f"v0 symbols drift across opt levels:\n"
        f"  opt-0: {sorted(opt0)}\n"
        f"  opt-3: {sorted(opt3)}"
    )


def test_v0_demangles_fully(run_analyzer, analyzer, tmp_path, monkeypatch):
    """(c) LLVM's demangler (wired in via reachability-analyzer
    --selftest-demangle) renders v0 symbols fully -- not just the legacy
    scheme -- on this toolchain."""
    tc = toolchain.check_coherence(analyzer)
    _require_rust_readable(tc)
    v0 = _work_symbols(tmp_path, tc, "v0_demangle", monkeypatch,
                       "-Csymbol-mangling-version=v0", optimize=False)
    assert v0
    for sym in v0:
        r = run_analyzer(["--selftest-demangle", sym])
        out = r.stdout.strip()
        assert out == "rust_generic::work::<u32>" or out == "rust_generic::work::<u64>", \
            f"v0 symbol {sym!r} did not demangle fully, got {out!r}"
