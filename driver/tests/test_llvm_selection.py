import os
import pathlib
import shutil
import subprocess


SELECTOR = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "select_llvm.sh"


def _toolchain_path(tmp_path, versions):
    for tool in ("readlink", "sort", "cut"):
        (tmp_path / tool).symlink_to(shutil.which(tool))
    for name, version in versions.items():
        tool = tmp_path / name
        tool.write_text(f"#!/bin/sh\nprintf '%s\\n' '{version}'\n")
        tool.chmod(0o755)
    return {**os.environ, "PATH": str(tmp_path)}


def _select(env, *args):
    return subprocess.run(
        ["/bin/bash", str(SELECTOR), *args],
        env=env,
        capture_output=True,
        text=True,
    )


def test_unversioned_llvm_config_is_accepted(tmp_path):
    env = _toolchain_path(tmp_path, {"llvm-config": "22.1.7"})

    major = _select(env)
    config = _select(env, "--config")

    assert (major.returncode, major.stdout.strip()) == (0, "22")
    assert (config.returncode, config.stdout.strip()) == (0, str(tmp_path / "llvm-config"))


def test_versioned_llvm_config_takes_precedence(tmp_path):
    env = _toolchain_path(
        tmp_path, {"llvm-config": "23.0.0", "llvm-config-22": "22.1.7"}
    )

    config = _select(env, "--config")

    assert (config.returncode, config.stdout.strip()) == (0, str(tmp_path / "llvm-config-22"))


def test_requested_major_can_use_unversioned_llvm_config(tmp_path):
    env = _toolchain_path(tmp_path, {"llvm-config": "21.1.7"})

    config = _select(env, "--config", "21")

    assert (config.returncode, config.stdout.strip()) == (0, str(tmp_path / "llvm-config"))


def test_unsupported_unversioned_llvm_config_is_rejected(tmp_path):
    env = _toolchain_path(tmp_path, {"llvm-config": "20.1.7"})

    result = _select(env, "--config")

    assert result.returncode == 1
    assert "LLVM >= 21" in result.stderr
