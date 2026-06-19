"""Toolchain discovery and LLVM version-coherence checking.

Version policy (LLVM 21 is the floor; newer is allowed):

- The analyzer is built against some LLVM major M. M must be >= MIN_LLVM_MAJOR.
- clang, clang++, llvm-link and opt must all share that same major M (a single
  coherent toolchain produces and merges the bitcode the analyzer reads).
- rustc's bundled LLVM major must be <= M. LLVM reads older bitcode (auto-upgrade)
  but not newer, so the analyzer/tools must be at least as new as every producer.

Any violation is a loud, fatal error -- no silent fallback.
"""

import os
import re
import shutil
import subprocess
from dataclasses import dataclass

MIN_LLVM_MAJOR = 21


class ToolchainError(RuntimeError):
    """Raised on a missing tool or an LLVM version mismatch."""


_RUSTC_MAJOR_RE = re.compile(r"LLVM version[:\s]+(\d+)", re.IGNORECASE)
_TOOL_MAJOR_RE = re.compile(r"version\s+(\d+)\.", re.IGNORECASE)


def _parse_llvm_major_from_rustc(text: str) -> int:
    m = _RUSTC_MAJOR_RE.search(text)
    if not m:
        raise ToolchainError(f"cannot parse LLVM version from rustc output:\n{text}")
    return int(m.group(1))


def _parse_llvm_major(text: str) -> int:
    m = _TOOL_MAJOR_RE.search(text)
    if not m:
        raise ToolchainError(f"cannot parse version from: {text!r}")
    return int(m.group(1))


def rustc_llvm_major() -> int:
    """Pinned LLVM major == the LLVM that rustc bundles."""
    out = subprocess.run(
        ["rustc", "-vV"], capture_output=True, text=True, check=True
    ).stdout
    return _parse_llvm_major_from_rustc(out)


def find_tool(name: str, env_var: str, versioned: str | None) -> str:
    """Resolve a tool path: explicit env var -> versioned name -> plain name."""
    if env_var and os.environ.get(env_var):
        return os.environ[env_var]
    for cand in ([versioned] if versioned else []) + [name]:
        p = shutil.which(cand)
        if p:
            return p
    raise ToolchainError(f"required tool not found: {name} (set ${env_var})")


def tool_llvm_major(path: str) -> int:
    out = subprocess.run(
        [path, "--version"], capture_output=True, text=True, check=True
    ).stdout
    return _parse_llvm_major(out)


def analyzer_llvm_major(path: str) -> int:
    """The analyzer's --version prints the LLVM major it was linked against."""
    out = subprocess.run(
        [path, "--version"], capture_output=True, text=True, check=True
    ).stdout
    m = re.search(r"LLVM\s+(\d+)", out)
    if not m:
        raise ToolchainError(f"analyzer --version lacks LLVM major:\n{out}")
    return int(m.group(1))


@dataclass
class Toolchain:
    clang: str
    clangxx: str
    llvm_link: str
    opt: str
    analyzer: str
    llvm_major: int  # the chosen toolchain major M (>= MIN_LLVM_MAJOR)
    rustc_major: int


def check_coherence(analyzer_path: str) -> Toolchain:
    """Resolve the toolchain around the analyzer's LLVM major and validate the
    version policy (see module docstring).

    The analyzer's own LLVM major M is authoritative; clang/clang++/llvm-link/opt
    are resolved for M (versioned names preferred) and must all match it. rustc's
    LLVM major must not exceed M. Raises ToolchainError on any violation.
    """
    M = analyzer_llvm_major(analyzer_path)
    if M < MIN_LLVM_MAJOR:
        raise ToolchainError(
            f"analyzer built against LLVM {M}, but the minimum supported is "
            f"{MIN_LLVM_MAJOR}. Rebuild it against LLVM >= {MIN_LLVM_MAJOR}."
        )

    clang = find_tool("clang", "CLANG", f"clang-{M}")
    clangxx = find_tool("clang++", "CLANGXX", f"clang++-{M}")
    llvm_link = find_tool("llvm-link", "LLVM_LINK", f"llvm-link-{M}")
    opt = find_tool("opt", "OPT", f"opt-{M}")

    mismatches = []
    for label, path, major in [
        ("clang", clang, tool_llvm_major(clang)),
        ("clang++", clangxx, tool_llvm_major(clangxx)),
        ("llvm-link", llvm_link, tool_llvm_major(llvm_link)),
        ("opt", opt, tool_llvm_major(opt)),
    ]:
        if major != M:
            mismatches.append(f"  {label} ({path}): LLVM {major}, analyzer is LLVM {M}")
    if mismatches:
        raise ToolchainError(
            "toolchain LLVM major mismatch (analyzer = %d):\n%s\n"
            "Set $CLANG/$CLANGXX/$LLVM_LINK/$OPT or install matching llvm-%d tools."
            % (M, "\n".join(mismatches), M)
        )

    rustc_major = rustc_llvm_major()
    if rustc_major > M:
        raise ToolchainError(
            f"rustc's bundled LLVM is {rustc_major}, newer than the analyzer "
            f"toolchain LLVM {M}. Newer bitcode cannot be read by older tools; "
            f"rebuild the analyzer/toolchain against LLVM >= {rustc_major}."
        )

    return Toolchain(clang, clangxx, llvm_link, opt, analyzer_path, M, rustc_major)
