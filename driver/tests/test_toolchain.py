import pytest

from reachability import toolchain


def test_parse_rustc_llvm_major():
    out = "rustc 1.94.0-nightly\nLLVM version: 21.1.8\n"
    assert toolchain._parse_llvm_major_from_rustc(out) == 21


def test_parse_tool_llvm_major():
    assert toolchain._parse_llvm_major("Ubuntu LLVM version 21.1.8") == 21
    assert toolchain._parse_llvm_major("clang version 21.1.8") == 21


def _patch(monkeypatch, *, analyzer, tools, rustc):
    monkeypatch.setattr(toolchain, "rustc_llvm_major", lambda: rustc)
    monkeypatch.setattr(toolchain, "find_tool", lambda *a, **k: "/usr/bin/" + a[0])
    monkeypatch.setattr(toolchain, "tool_llvm_major", lambda p: tools)
    monkeypatch.setattr(toolchain, "analyzer_llvm_major", lambda p: analyzer)


def test_check_coherence_raises_on_tool_mismatch(monkeypatch):
    monkeypatch.setattr(toolchain, "rustc_llvm_major", lambda: 21)
    monkeypatch.setattr(toolchain, "find_tool", lambda *a, **k: "/usr/bin/" + a[0])
    monkeypatch.setattr(toolchain, "tool_llvm_major", lambda p: 18 if "opt" in p else 21)
    monkeypatch.setattr(toolchain, "analyzer_llvm_major", lambda p: 21)
    with pytest.raises(toolchain.ToolchainError):
        toolchain.check_coherence("/fake/analyzer")


def test_check_coherence_passes_on_llvm21(monkeypatch):
    _patch(monkeypatch, analyzer=21, tools=21, rustc=21)
    tc = toolchain.check_coherence("/fake/analyzer")
    assert tc.llvm_major == 21 and tc.rustc_major == 21


def test_check_coherence_allows_newer_llvm(monkeypatch):
    # Analyzer + tools on LLVM 23, rustc on 21: allowed (23 reads 21 bitcode).
    _patch(monkeypatch, analyzer=23, tools=23, rustc=21)
    tc = toolchain.check_coherence("/fake/analyzer")
    assert tc.llvm_major == 23


def test_check_coherence_rejects_below_minimum(monkeypatch):
    _patch(monkeypatch, analyzer=20, tools=20, rustc=20)
    with pytest.raises(toolchain.ToolchainError):
        toolchain.check_coherence("/fake/analyzer")


def test_check_coherence_rejects_rustc_newer_than_toolchain(monkeypatch):
    # Analyzer LLVM 21 cannot read rustc's hypothetical LLVM 22 bitcode.
    _patch(monkeypatch, analyzer=21, tools=21, rustc=22)
    with pytest.raises(toolchain.ToolchainError):
        toolchain.check_coherence("/fake/analyzer")
