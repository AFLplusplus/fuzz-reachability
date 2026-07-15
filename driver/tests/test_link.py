import pytest

from reachability import link
from reachability.toolchain import Toolchain


def _tc():
    return Toolchain("clang", "clang++", "llvm-link", "analyzer", 21, 21)


def test_link_empty_raises():
    with pytest.raises(link.LinkError):
        link.link_bitcode([], "/tmp/out.bc", _tc())


def test_link_cmd_built(monkeypatch, tmp_path):
    import subprocess

    captured = {}

    class R:
        returncode = 0
        stderr = ""
        stdout = ""

    def fake_run(cmd, **k):
        captured["cmd"] = cmd
        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    inputs = [tmp_path / name for name in ("a.bc", "b.bc", "c.bc")]
    for index, path in enumerate(inputs):
        path.write_bytes(bytes([index]))
    out = tmp_path / "out.bc"
    link.link_bitcode([str(path) for path in inputs], str(out), _tc())
    assert captured["cmd"][0] == "llvm-link"
    assert "-o" in captured["cmd"] and str(out) in captured["cmd"]
    assert all(str(path) in captured["cmd"] for path in inputs)
    assert not any(a.startswith("--override=") for a in captured["cmd"])


def test_duplicate_definition_failure_is_reported(monkeypatch, tmp_path):
    import subprocess

    class R:
        returncode = 1
        stderr = "error: Linking globals named 'same': symbol multiply defined!"
        stdout = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    inputs = [tmp_path / "a.bc", tmp_path / "b.bc"]
    inputs[0].write_bytes(b"a")
    inputs[1].write_bytes(b"b")
    with pytest.raises(link.LinkError, match="symbol multiply defined"):
        link.link_bitcode([str(path) for path in inputs], str(tmp_path / "out.bc"), _tc())


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
        stdout = ""

    def fake_run(cmd, **k):
        captured["cmd"] = cmd
        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    link.link_bitcode([str(a), str(b)], str(tmp_path / "out.bc"), _tc())
    assert captured["cmd"].count(str(a)) + captured["cmd"].count(str(b)) == 1


def test_unreadable_input_is_link_error(tmp_path):
    missing = tmp_path / "missing.bc"
    with pytest.raises(link.LinkError, match="cannot read bitcode input"):
        link.link_bitcode([str(missing)], str(tmp_path / "out.bc"), _tc())


def test_link_spawn_failure_is_domain_error(tmp_path, monkeypatch):
    source = tmp_path / "input.bc"
    source.write_bytes(b"bc")

    def fail(*args, **kwargs):
        raise FileNotFoundError("missing")

    monkeypatch.setattr(link.subprocess, "run", fail)
    with pytest.raises(link.LinkError, match="cannot run llvm-link"):
        link.link_bitcode([str(source)], str(tmp_path / "out.bc"), _tc())


def test_link_batches_large_input_sets(tmp_path, monkeypatch):
    inputs = []
    for index in range(7):
        path = tmp_path / f"{index}.bc"
        path.write_bytes(str(index).encode())
        inputs.append(str(path))
    calls = []

    class R:
        returncode = 0
        stderr = b""
        stdout = b""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        output = cmd[cmd.index("-o") + 1]
        with open(output, "wb") as fh:
            fh.write(b"linked")
        return R()

    monkeypatch.setattr(link.subprocess, "run", fake_run)
    link.link_bitcode(inputs, str(tmp_path / "out.bc"), _tc(), batch_size=3)
    assert len(calls) == 4
    assert all(len(call) <= 6 for call in calls)


def test_link_batches_thousands_of_modules(tmp_path, monkeypatch):
    inputs = []
    for index in range(2001):
        path = tmp_path / f"{index}.bc"
        path.write_bytes(str(index).encode())
        inputs.append(str(path))
    calls = []

    class R:
        returncode = 0
        stderr = b""
        stdout = b""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        output = cmd[cmd.index("-o") + 1]
        with open(output, "wb") as fh:
            fh.write(b"linked")
        return R()

    monkeypatch.setattr(link.subprocess, "run", fake_run)
    link.link_bitcode(inputs, str(tmp_path / "out.bc"), _tc(), batch_size=50)
    assert len(calls) > 40
    assert all(len(call) <= 53 for call in calls)
