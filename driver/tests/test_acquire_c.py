from reachability import acquire_c


def test_build_env_sets_wrappers(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    env = acquire_c._build_env(clang_bindir="/usr/lib/llvm-21/bin")
    assert env["CC"].endswith("gclang")
    assert env["CXX"].endswith("gclang++")
    assert env["LLVM_COMPILER_PATH"] == "/usr/lib/llvm-21/bin"
