"""End-to-end proof that reached.txt / not_reached.txt actually drive clang's
SanitizerCoverage allowlist / ignorelist.

For each fixture we: compile to bitcode, run the analyzer to emit the two lists,
then recompile with `-fsanitize-coverage=func,trace-pc-guard` plus the lists and
inspect the emitted IR to confirm exactly the intended functions are
instrumented. This validates the format (lowercase `fun:`, `src:*` for the
allowlist) and that clang matches the *mangled* names we write.
"""

import json
import os
import re
import shlex
import subprocess
import types

import pytest

from conftest import FIXTURES
from reachability import acquire_c, toolchain


@pytest.fixture(scope="module")
def llvm(analyzer):
    """clang, clang++ and llvm-link of the analyzer's LLVM major, each None
    when not installed: the analyzer cannot read newer bitcode, and Apple
    clang never matches."""
    major = toolchain.analyzer_llvm_major(analyzer)

    def resolve(name, env_var):
        try:
            path = toolchain.find_tool(name, env_var, f"{name}-{major}", major)
            return path if toolchain.tool_llvm_major(path) == major else None
        except toolchain.ToolchainError:
            return None

    return types.SimpleNamespace(
        clang=resolve("clang", "CLANG"), clangxx=resolve("clang++", "CLANGXX"),
        llvm_link=resolve("llvm-link", "LLVM_LINK"), major=major)


def _need(llvm, *names):
    missing = [n for n in names if getattr(llvm, n) is None]
    if missing:
        pytest.skip(f"{', '.join(missing)} for LLVM {llvm.major} not installed")


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


def test_c_allowlist_and_ignorelist(analyzer, llvm, tmp_path):
    _need(llvm, "clang")
    src = os.path.join(FIXTURES, "c_direct", "main.c")
    reached, notr = _gen_lists(analyzer, llvm.clang, src, tmp_path)
    assert "src:*" in reached.read_text()           # allowlist needs the src line
    assert "fun:used_a" in reached.read_text()
    assert "fun:dead_fn" in notr.read_text()

    # allowlist: only reachable functions are instrumented.
    al = _sancov_ir(llvm.clang, src, "-fsanitize-coverage-allowlist", reached, tmp_path / "a.ll")
    assert {"used_a", "used_b", "LLVMFuzzerTestOneInput"} <= al
    assert "dead_fn" not in al

    # ignorelist: unreachable functions are NOT instrumented; the rest are.
    ig = _sancov_ir(llvm.clang, src, "-fsanitize-coverage-ignorelist", notr, tmp_path / "i.ll")
    assert "dead_fn" not in ig
    assert {"used_a", "used_b", "LLVMFuzzerTestOneInput"} <= ig


def test_source_faithful_bitcode_keeps_always_inline_helper(analyzer, llvm, tmp_path,
                                                             monkeypatch):
    _need(llvm, "clang")
    source = tmp_path / "inline.c"
    source.write_text(
        "static inline __attribute__((always_inline)) int helper(int x) { return x + 1; }\n"
        "int LLVMFuzzerTestOneInput(const unsigned char *data, unsigned long size) "
        "{ return size ? helper(data[0]) : 0; }\n"
    )
    monkeypatch.delenv("LLVM_BITCODE_GENERATION_FLAGS", raising=False)
    flags = shlex.split(acquire_c._build_env("/usr/lib/llvm-21/bin")[
        "LLVM_BITCODE_GENERATION_FLAGS"])
    bitcode = tmp_path / "inline.bc"
    subprocess.run([llvm.clang, *flags, "-g", "-emit-llvm", "-c", str(source),
                    "-o", str(bitcode)], check=True)
    reached = tmp_path / "reached.txt"
    ignored = tmp_path / "not_reached.txt"
    report = tmp_path / "report.json"
    subprocess.run([analyzer, str(bitcode), "--entry", "LLVMFuzzerTestOneInput",
                    "--reached-out", str(reached), "--not-reached-out", str(ignored),
                    "--out", str(report)], check=True)
    names = {f["mangled"] for f in json.loads(report.read_text())["reachable"]}
    assert "helper" in names
    assert "fun:helper" in reached.read_text().splitlines()
    assert "fun:helper" not in ignored.read_text().splitlines()


def test_cpp_allowlist_matches_mangled(analyzer, llvm, tmp_path):
    _need(llvm, "clangxx")
    # Proves clang matches the *mangled* names we emit (A::run -> _ZN1A3runEi).
    src = os.path.join(FIXTURES, "cpp_virtual", "main.cpp")
    reached, _ = _gen_lists(analyzer, llvm.clangxx, src, tmp_path)
    text = reached.read_text()
    assert "fun:_ZN1A3runEi" in text and "fun:_ZN1B3runEi" in text  # mangled

    al = _sancov_ir(llvm.clangxx, src, "-fsanitize-coverage-allowlist", reached, tmp_path / "a.ll")
    assert {"_ZN1A3runEi", "_ZN1B3runEi"} <= al  # both overrides instrumented


def test_static_name_collision_stays_instrumented(analyzer, llvm, tmp_path):
    _need(llvm, "clang", "llvm_link")
    sources = {
        "a.c": "static int helper(int x) { return x + 1; }\n"
               "int a_entry(int x) { return helper(x); }\n",
        "b.c": "static int helper(int x) { return x * 2; }\n"
               "int b_unused(int x) { return helper(x); }\n",
        "main.c": "int a_entry(int);\n"
                  "int LLVMFuzzerTestOneInput(const unsigned char *d, unsigned long n)"
                  " { return a_entry((int)n); }\n",
    }
    bcs = []
    for name in ("b.c", "a.c", "main.c"):
        src = tmp_path / name
        src.write_text(sources[name])
        bc = tmp_path / (name + ".bc")
        subprocess.run([llvm.clang, "-g", "-O0", "-fno-inline", "-emit-llvm", "-c",
                        str(src), "-o", str(bc)], check=True)
        bcs.append(str(bc))
    merged = tmp_path / "merged.bc"
    subprocess.run([llvm.llvm_link, *bcs, "-o", str(merged)], check=True)
    reached, notr = tmp_path / "reached.txt", tmp_path / "not_reached.txt"
    report = tmp_path / "r.json"
    subprocess.run(
        [analyzer, str(merged), "--entry", "LLVMFuzzerTestOneInput",
         "--reached-out", str(reached), "--not-reached-out", str(notr),
         "--out", str(report)],
        check=True,
    )
    data = json.loads(report.read_text())
    reachable = {(os.path.basename(f["file"]), f["mangled"]): f
                 for f in data["reachable"]}
    renamed = reachable[("a.c", "helper")]
    assert renamed["ir_name"] == "helper.1"
    assert renamed["key"] == "helper"

    a_src = str(tmp_path / "a.c")
    al = _sancov_ir(llvm.clang, a_src, "-fsanitize-coverage-allowlist", reached,
                    tmp_path / "a_allow.ll")
    ig = _sancov_ir(llvm.clang, a_src, "-fsanitize-coverage-ignorelist", notr,
                    tmp_path / "a_ignore.ll")
    assert "helper" in al
    assert "helper" in ig
