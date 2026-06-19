"""End-to-end proof that reached.txt / not_reached.txt actually drive clang's
SanitizerCoverage allowlist / ignorelist.

For each fixture we: compile to bitcode, run the analyzer to emit the two lists,
then recompile with `-fsanitize-coverage=func,trace-pc-guard` plus the lists and
inspect the emitted IR to confirm exactly the intended functions are
instrumented. This validates the format (lowercase `fun:`, `src:*` for the
allowlist) and that clang matches the *mangled* names we write.
"""

import os
import re
import shutil
import subprocess

import pytest

from conftest import FIXTURES

CLANG = shutil.which("clang-21") or shutil.which("clang")
CLANGXX = shutil.which("clang++-21") or shutil.which("clang++")


def _instrumented(ll_path):
    """Names of defined functions whose body calls the sancov guard."""
    out, name, inst = set(), None, False
    for line in open(ll_path):
        if line.startswith("define "):
            m = re.search(r"@([\w.$]+)\(", line)
            name, inst = (m.group(1) if m else None), False
        elif "__sanitizer_cov_trace_pc_guard" in line and name:
            inst = True
        elif line.startswith("}") and name:
            if inst:
                out.add(name)
            name = None
    return out


def _gen_lists(analyzer, compiler, src, tmp_path):
    bc = tmp_path / "m.bc"
    subprocess.run(
        [compiler, "-g", "-O0", "-fno-inline", "-emit-llvm", "-c", src, "-o", str(bc)],
        check=True,
    )
    reached, notr = tmp_path / "reached.txt", tmp_path / "not_reached.txt"
    subprocess.run(
        [analyzer, str(bc), "--entry", "LLVMFuzzerTestOneInput",
         "--reached-out", str(reached), "--not-reached-out", str(notr),
         "--out", str(tmp_path / "r.json")],
        check=True,
    )
    return reached, notr


def _sancov_ir(compiler, src, list_flag, list_path, out_ll):
    subprocess.run(
        [compiler, "-O1", "-fno-inline", "-fsanitize-coverage=func,trace-pc-guard",
         f"{list_flag}={list_path}", src, "-S", "-emit-llvm", "-o", str(out_ll)],
        check=True,
    )
    return _instrumented(out_ll)


@pytest.mark.skipif(not CLANG, reason="clang not installed")
def test_c_allowlist_and_ignorelist(analyzer, tmp_path):
    src = os.path.join(FIXTURES, "c_direct", "main.c")
    reached, notr = _gen_lists(analyzer, CLANG, src, tmp_path)
    assert "src:*" in reached.read_text()           # allowlist needs the src line
    assert "fun:used_a" in reached.read_text()
    assert "fun:dead_fn" in notr.read_text()

    # allowlist: only reachable functions are instrumented.
    al = _sancov_ir(CLANG, src, "-fsanitize-coverage-allowlist", reached, tmp_path / "a.ll")
    assert {"used_a", "used_b", "LLVMFuzzerTestOneInput"} <= al
    assert "dead_fn" not in al

    # ignorelist: unreachable functions are NOT instrumented; the rest are.
    ig = _sancov_ir(CLANG, src, "-fsanitize-coverage-ignorelist", notr, tmp_path / "i.ll")
    assert "dead_fn" not in ig
    assert {"used_a", "used_b", "LLVMFuzzerTestOneInput"} <= ig


@pytest.mark.skipif(not CLANGXX, reason="clang++ not installed")
def test_cpp_allowlist_matches_mangled(analyzer, tmp_path):
    # Proves clang matches the *mangled* names we emit (A::run -> _ZN1A3runEi).
    src = os.path.join(FIXTURES, "cpp_virtual", "main.cpp")
    reached, _ = _gen_lists(analyzer, CLANGXX, src, tmp_path)
    text = reached.read_text()
    assert "fun:_ZN1A3runEi" in text and "fun:_ZN1B3runEi" in text  # mangled

    al = _sancov_ir(CLANGXX, src, "-fsanitize-coverage-allowlist", reached, tmp_path / "a.ll")
    assert {"_ZN1A3runEi", "_ZN1B3runEi"} <= al  # both overrides instrumented
