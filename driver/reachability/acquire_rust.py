"""Rust bitcode acquisition via rustc --emit=llvm-bc.

With codegen-units=1, rustc emits one .bc per crate into target/<profile>/deps/.
The final link step may fail under --emit=llvm-bc; that is fine -- we only need
the .bc files, so collection proceeds regardless of the build's exit status.
"""

import glob
import os
import subprocess


class AcquireError(RuntimeError):
    pass


def _rustflags(build_std: bool) -> str:
    flags = "--emit=llvm-bc -Cembed-bitcode=yes -Ccodegen-units=1"
    if build_std:
        flags += " -Zbuild-std"
    return flags


def acquire_rust_bitcode(project_dir, profile="debug", build_std=False):
    """Build the Rust project and collect every emitted .bc.

    Returns a list of .bc paths. Raises AcquireError if none were produced.
    """
    env = dict(os.environ)
    env["RUSTFLAGS"] = _rustflags(build_std)
    cmd = ["cargo", "build"]
    if profile == "release":
        cmd.append("--release")
    if build_std:
        cmd += ["-Zbuild-std", "--target", "x86_64-unknown-linux-gnu"]
    # Tolerate link failure: we only consume the .bc files.
    subprocess.run(cmd, cwd=project_dir, env=env, capture_output=True, text=True)

    patterns = [os.path.join(project_dir, "target", profile, "deps", "*.bc")]
    if build_std:
        patterns.append(
            os.path.join(project_dir, "target", "*", profile, "deps", "*.bc")
        )
    bcs = []
    for pat in patterns:
        bcs.extend(glob.glob(pat))
    if not bcs:
        raise AcquireError(f"no .bc produced under {project_dir}/target/{profile}/deps/")
    return sorted(set(bcs))
