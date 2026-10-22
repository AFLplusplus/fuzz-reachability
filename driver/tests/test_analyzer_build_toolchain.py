import os
from pathlib import Path
import shutil
import subprocess


ANALYZER = Path(__file__).resolve().parents[2] / "analyzer"


def _tool(tmp_path, name, body):
    path = tmp_path / "bin" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


def _check(tmp_path, clang_version):
    llvm_bindir = tmp_path / "llvm" / "bin"
    llvm_bindir.mkdir(parents=True)
    config = _tool(
        tmp_path,
        "llvm-config",
        f'case "$1" in\n  --version) echo 21.1.8;;\n  --bindir) echo "{llvm_bindir}";;\nesac\n',
    )
    clang = _tool(tmp_path, "clang++", f"echo 'clang version {clang_version}'\n")
    for name in ("grep", "sed"):
        (clang.parent / name).symlink_to(shutil.which(name))
    env = {**os.environ, "PATH": str(clang.parent)}
    for name in ("MAKEFLAGS", "MFLAGS", "GNUMAKEFLAGS"):
        env.pop(name, None)
    result = subprocess.run(
        [shutil.which("make"), "-s", "-C", str(ANALYZER), "check-llvm", "print-config", f"LLVM_CONFIG={config}"],
        env=env,
        capture_output=True,
        text=True,
    )
    return result, clang


def test_clang_in_separate_package_is_accepted(tmp_path):
    result, clang = _check(tmp_path, "21.1.8")

    assert result.returncode == 0, result.stderr
    assert f"CXX={clang}" in result.stdout


def test_clang_from_wrong_llvm_major_is_rejected(tmp_path):
    result, _ = _check(tmp_path, "22.0.0")

    assert result.returncode != 0
    assert "LLVM 21" in result.stderr


def test_scan_build_checks_real_clang_behind_analyzer_wrapper(tmp_path):
    llvm_bindir = tmp_path / "llvm" / "bin"
    llvm_bindir.mkdir(parents=True)
    config = _tool(
        tmp_path,
        "llvm-config",
        f'case "$1" in\n  --version) echo 21.1.8;;\n  --bindir) echo "{llvm_bindir}";;\nesac\n',
    )
    clang = llvm_bindir / "clang++"
    clang.write_text("#!/bin/sh\necho 'clang version 21.1.8'\n")
    clang.chmod(0o755)
    wrapper = _tool(tmp_path, "c++-analyzer", "exit 0\n")
    scan_build = llvm_bindir / "scan-build"
    scan_build.write_text(
        "#!/bin/sh\n"
        '[ "$1" = "--status-bugs" ] || exit 10\n'
        "shift\n"
        f'[ "$1" = "--use-c++={clang}" ] || exit 11\n'
        "shift\n"
        f'"$1" "$2" "$3" "$4" "CXX={wrapper}" check-llvm\n'
    )
    scan_build.chmod(0o755)
    bin_dir = tmp_path / "bin"
    for name in ("grep", "sed", "make"):
        (bin_dir / name).symlink_to(shutil.which(name))
    env = {**os.environ, "PATH": str(bin_dir)}
    for name in ("MAKEFLAGS", "MFLAGS", "GNUMAKEFLAGS"):
        env.pop(name, None)
    result = subprocess.run(
        [shutil.which("make"), "-s", "-C", str(ANALYZER), "scan-build", f"LLVM_CONFIG={config}"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
