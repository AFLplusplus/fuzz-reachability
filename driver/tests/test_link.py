import pytest

from reachability import link
from reachability.toolchain import Toolchain


def _tc():
    return Toolchain("clang", "clang++", "llvm-link", "opt", "analyzer", 21, 21)


def test_link_empty_raises():
    with pytest.raises(link.LinkError):
        link.link_bitcode([], "/tmp/out.bc", _tc())


def test_link_cmd_built(monkeypatch):
    import subprocess

    captured = {}

    class R:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **k):
        captured["cmd"] = cmd
        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    link.link_bitcode(["a.bc", "b.bc"], "out.bc", _tc())
    assert captured["cmd"][0] == "llvm-link"
    assert "-o" in captured["cmd"] and "out.bc" in captured["cmd"]
