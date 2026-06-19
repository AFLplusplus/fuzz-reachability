"""Toolchain discovery and LLVM version-coherence checking.

Version policy (LLVM 21 is the floor; newer is allowed):

- The analyzer is built against some LLVM major M. M must be >= MIN_LLVM_MAJOR.
- clang, clang++, llvm-link and opt must all share that same major M (a single
  coherent toolchain produces and merges the bitcode the analyzer reads).
- rustc's bundled LLVM major must be <= M. LLVM reads older bitcode (auto-upgrade)
  but not newer, so the analyzer/tools must be at least as new as every producer.
  The major check is a coarse gate; reading rustc's bitcode actually requires the
  tools' *full* version to be >= rustc's full LLVM version. A same-major distro
  LLVM that is an older patch release than rustc's cannot read it (llvm-link:
  "Invalid record"). That full-version requirement is enforced on the Rust path
  via rust_bitcode_readable / assert_rust_bitcode_readable.

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
_RUSTC_FULL_RE = re.compile(
    r"LLVM version[:\s]+(\d+)(?:\.(\d+))?(?:\.(\d+))?", re.IGNORECASE
)
_TOOL_FULL_RE = re.compile(
    r"version\s+(\d+)(?:\.(\d+))?(?:\.(\d+))?", re.IGNORECASE
)


def _version_tuple(m) -> tuple:
    return tuple(int(g) if g else 0 for g in m.groups())


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


def rustc_llvm_version() -> tuple:
    """Full (major, minor, patch) of the LLVM that rustc bundles -- the version
    of the bitcode rustc emits."""
    out = subprocess.run(
        ["rustc", "-vV"], capture_output=True, text=True, check=True
    ).stdout
    m = _RUSTC_FULL_RE.search(out)
    if not m:
        raise ToolchainError(f"cannot parse LLVM version from rustc output:\n{out}")
    return _version_tuple(m)


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


def tool_llvm_version(path: str) -> tuple:
    """Full (major, minor, patch) reported by an LLVM tool's --version."""
    out = subprocess.run(
        [path, "--version"], capture_output=True, text=True, check=True
    ).stdout
    m = _TOOL_FULL_RE.search(out)
    if not m:
        raise ToolchainError(f"cannot parse version from: {out!r}")
    return _version_tuple(m)


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


def rust_bitcode_readable(tc: Toolchain) -> bool:
    """True if the toolchain's LLVM is new enough to read rustc's bitcode.

    rustc emits bitcode at its bundled LLVM's full version; llvm-link/opt (and the
    analyzer, built against the same LLVM package) must be at least that full
    version. Same major is insufficient: a distro LLVM that is an older patch
    release than rustc's cannot read the newer bitcode.
    """
    consumer = min(tool_llvm_version(tc.llvm_link), tool_llvm_version(tc.opt))
    return consumer >= rustc_llvm_version()


def assert_rust_bitcode_readable(tc: Toolchain) -> None:
    """Raise ToolchainError if the toolchain cannot read rustc's bitcode."""
    if rust_bitcode_readable(tc):
        return
    rv = rustc_llvm_version()
    cv = min(tool_llvm_version(tc.llvm_link), tool_llvm_version(tc.opt))
    fmt = lambda t: ".".join(str(x) for x in t)
    raise ToolchainError(
        f"rustc's bundled LLVM ({fmt(rv)}) is newer than the analyzer toolchain's "
        f"LLVM ({fmt(cv)}); newer bitcode cannot be read by older tools. Rebuild "
        f"the analyzer against an LLVM whose full version is >= rustc's, e.g. "
        f"`make build LLVM_MAJOR={rv[0] + 1}` (the default auto-selects such a "
        f"major; see scripts/select_llvm.sh)."
    )
