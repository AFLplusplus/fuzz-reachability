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
    monkeypatch.setattr(toolchain, "tool_llvm_major", lambda p: 18 if "llvm-link" in p else 21)
    monkeypatch.setattr(toolchain, "analyzer_llvm_major", lambda p: 21)
    with pytest.raises(toolchain.ToolchainError):
        toolchain.check_coherence("/fake/analyzer")


def test_check_coherence_passes_on_llvm21(monkeypatch):
    _patch(monkeypatch, analyzer=21, tools=21, rustc=21)
    tc = toolchain.check_coherence("/fake/analyzer", require_rust=True)
    assert tc.llvm_major == 21 and tc.rustc_major == 21


def test_check_coherence_allows_newer_llvm(monkeypatch):
    # Analyzer + tools on LLVM 23, rustc on 21: allowed (23 reads 21 bitcode).
    _patch(monkeypatch, analyzer=23, tools=23, rustc=21)
    tc = toolchain.check_coherence("/fake/analyzer", require_rust=True)
    assert tc.llvm_major == 23


def test_check_coherence_rejects_below_minimum(monkeypatch):
    _patch(monkeypatch, analyzer=20, tools=20, rustc=20)
    with pytest.raises(toolchain.ToolchainError):
        toolchain.check_coherence("/fake/analyzer")


def test_check_coherence_rejects_rustc_newer_than_toolchain(monkeypatch):
    # Analyzer LLVM 21 cannot read rustc's hypothetical LLVM 22 bitcode.
    _patch(monkeypatch, analyzer=21, tools=21, rustc=22)
    with pytest.raises(toolchain.ToolchainError):
        toolchain.check_coherence("/fake/analyzer", require_rust=True)


def _fake_tool(directory, name, major):
    path = directory / name
    path.write_text(f"#!/bin/sh\necho 'clang version {major}.1.8'\n")
    path.chmod(0o755)
    return str(path)


def test_find_tool_prefers_plain_name_of_matching_major(monkeypatch, tmp_path):
    plain = _fake_tool(tmp_path, "clang", 21)
    _fake_tool(tmp_path, "clang-21", 21)
    monkeypatch.delenv("CLANG", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert toolchain.find_tool("clang", "CLANG", "clang-21", 21) == plain


def test_find_tool_falls_back_to_versioned_name_of_matching_major(monkeypatch, tmp_path):
    _fake_tool(tmp_path, "clang", 18)
    versioned = _fake_tool(tmp_path, "clang-21", 21)
    monkeypatch.delenv("CLANG", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert toolchain.find_tool("clang", "CLANG", "clang-21", 21) == versioned


def test_find_tool_returns_plain_name_when_no_major_matches(monkeypatch, tmp_path):
    plain = _fake_tool(tmp_path, "clang", 18)
    _fake_tool(tmp_path, "clang-21", 19)
    monkeypatch.delenv("CLANG", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert toolchain.find_tool("clang", "CLANG", "clang-21", 21) == plain


def test_check_coherence_selects_plain_tools_of_analyzer_major(monkeypatch, tmp_path):
    for name in ("clang", "clang-21", "clang++", "clang++-21",
                 "llvm-link", "llvm-link-21"):
        _fake_tool(tmp_path, name, 21)
    for var in ("CLANG", "CLANGXX", "LLVM_LINK"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(toolchain, "analyzer_llvm_major", lambda p: 21)
    tc = toolchain.check_coherence("/fake/analyzer")
    assert (tc.clang, tc.clangxx, tc.llvm_link) == tuple(
        str(tmp_path / name) for name in ("clang", "clang++", "llvm-link"))


def test_check_coherence_c_only_skips_rustc(monkeypatch):
    _patch(monkeypatch, analyzer=21, tools=21, rustc=99)
    monkeypatch.setattr(
        toolchain, "rustc_llvm_major",
        lambda: (_ for _ in ()).throw(AssertionError("rustc must not run")),
    )
    tc = toolchain.check_coherence("/fake/analyzer", require_rust=False)
    assert tc.rustc_major is None


def test_version_probe_spawn_failure_is_domain_error(monkeypatch):
    def fail(*args, **kwargs):
        raise FileNotFoundError("missing")

    monkeypatch.setattr(toolchain.subprocess, "run", fail)
    with pytest.raises(toolchain.ToolchainError, match="cannot run clang"):
        toolchain._run_version(["clang", "--version"], "clang")


def test_version_probe_nonzero_invalid_bytes_is_domain_error(monkeypatch):
    result = type("R", (), {
        "returncode": 2, "stdout": b"", "stderr": b"failure \xff",
    })()
    monkeypatch.setattr(toolchain.subprocess, "run", lambda *a, **k: result)
    with pytest.raises(toolchain.ToolchainError) as exc:
        toolchain._run_version(["clang", "--version"], "clang")
    assert "failure \ufffd" in str(exc.value)
