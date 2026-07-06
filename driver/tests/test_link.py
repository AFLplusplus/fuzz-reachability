import pytest

from reachability import link
from reachability.toolchain import Toolchain


def _tc():
    return Toolchain("clang", "clang++", "llvm-link", "analyzer", 21, 21)


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
    link.link_bitcode(["a.bc", "b.bc", "c.bc"], "out.bc", _tc())
    assert captured["cmd"][0] == "llvm-link"
    assert "-o" in captured["cmd"] and "out.bc" in captured["cmd"]
    assert "a.bc" in captured["cmd"]
    assert "b.bc" in captured["cmd"] and "c.bc" in captured["cmd"]
    assert not any(a.startswith("--override=") for a in captured["cmd"])


def test_duplicate_definition_failure_is_reported(monkeypatch):
    import subprocess

    class R:
        returncode = 1
        stderr = "error: Linking globals named 'same': symbol multiply defined!"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    with pytest.raises(link.LinkError, match="symbol multiply defined"):
        link.link_bitcode(["a.bc", "b.bc"], "out.bc", _tc())


def test_identical_modules_are_linked_once(tmp_path, monkeypatch):
    import subprocess

    a = tmp_path / "a.bc"
    b = tmp_path / "b.bc"
    a.write_bytes(b"same")
    b.write_bytes(b"same")
    captured = {}

    class R:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **k):
        captured["cmd"] = cmd
        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    link.link_bitcode([str(a), str(b)], str(tmp_path / "out.bc"), _tc())
    assert captured["cmd"].count(str(a)) + captured["cmd"].count(str(b)) == 1
