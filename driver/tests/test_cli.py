import json
import os
import shutil

import pytest

from conftest import FIXTURES
from reachability import cli

HAVE_GLLVM = shutil.which("gclang") is not None


def test_check_toolchain_ok(analyzer, monkeypatch):
    monkeypatch.setenv("REACHABILITY_ANALYZER", analyzer)
    assert cli.main(["check-toolchain"]) == 0


@pytest.mark.skipif(not HAVE_GLLVM, reason="gllvm not installed")
def test_run_c_direct(analyzer, tmp_path, monkeypatch):
    monkeypatch.setenv("REACHABILITY_ANALYZER", analyzer)
    work = tmp_path / "c_direct"
    shutil.copytree(os.path.join(FIXTURES, "c_direct"), work)
    out = tmp_path / "r.json"
    rc = cli.main(["run", "--project", str(work), "--lang", "c", "--out", str(out)])
    assert rc == 0
    result = json.load(open(out))
    reachable = {f["mangled"] for f in result["reachable"]}
    assert {"LLVMFuzzerTestOneInput", "used_a", "used_b"} <= reachable
    assert "dead_fn" not in reachable
