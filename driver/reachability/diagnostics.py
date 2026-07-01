"""Post-mortem diagnosis of a failed C/C++ bitcode acquisition.

When the build succeeds but no usable bitcode can be extracted, the raw
get-bc / gllvm output rarely names the cause. diagnose_build matches the captured
build log and the get-bc stderrs against an ordered table of known failure
fingerprints and returns a (cause, remedy) pair for the first match, so the caller
can turn a generic "no bitcode" error into an actionable one. Pure and I/O-free,
so every rule is unit-testable against a captured-log fixture.
"""

import re
from typing import Optional, Tuple

_LTO_SKIP = re.compile(
    r"skipping bitcode generation because.*link time optimization", re.I)
_GETBC_NO_SECTION = re.compile(r"Error reading the \.llvm_bc section", re.I)
_FLTO = re.compile(r"-flto\b")
_ASM_OR_NOINPUT = re.compile(
    r"skipping bitcode generation because.*(assembly|did not see any input)", re.I)
_CCACHE = re.compile(r"\b(s?ccache)\b")

_LTO_REMEDY = (
    "The build enables link-time optimization (-flto); gllvm skips embedding "
    "bitcode under LTO and ignores -fno-lto, so -flto must be removed, not "
    "counteracted. Per build system: CMake -DCMAKE_INTERPROCEDURAL_OPTIMIZATION="
    "OFF, Meson -Db_lto=false, configure --disable-lto; a plain Makefile has no "
    "standard switch, so null the project's LTO variable (e.g. AFL++ "
    "'make CFLAGS_FLTO='). Auto-detected builds already try these; an explicit "
    "--build-cmd must strip -flto itself."
)
_AFL_CC_REMEDY = (
    "The artifact has a .llvm_bc section name but no usable bitcode -- typical of "
    "a binary built by afl-clang-fast or clang -flto rather than gllvm. Rebuild "
    "the target from source with gclang/gclang++ (via --build-cmd, or CC=gclang "
    "for its Makefile)."
)
_CCACHE_REMEDY = (
    "A compiler cache (ccache/sccache) is in the build; it replays cached objects "
    "without re-running the gllvm wrapper, so no bitcode is embedded. Clear it "
    "(ccache -C) or disable it, then rebuild."
)
_ASM_REMEDY = (
    "gllvm skipped some translation units because they are assembly-only or had "
    "no input files; those carry no bitcode. Expected, and only a problem if the "
    "entry function lives in such a unit."
)


def diagnose_build(build_log: str, getbc_stderrs) -> Optional[Tuple[str, str]]:
    """Return (cause, remedy) for the first matching failure fingerprint, else None.

    build_log: combined stdout+stderr of the build command. getbc_stderrs: get-bc
    stderr strings collected while trying each artifact. Rules are ordered
    most-specific first and this is only called on the failure path.
    """
    log = build_log or ""
    no_section = any(_GETBC_NO_SECTION.search(e or "") for e in (getbc_stderrs or []))

    if _LTO_SKIP.search(log) or (no_section and _FLTO.search(log)):
        return ("link-time optimization strips gllvm bitcode", _LTO_REMEDY)
    if no_section:
        return ("the artifact has an empty/name-only .llvm_bc section", _AFL_CC_REMEDY)
    if _CCACHE.search(log):
        return ("a compiler cache bypassed the gllvm wrapper", _CCACHE_REMEDY)
    if _ASM_OR_NOINPUT.search(log):
        return ("some translation units produced no bitcode", _ASM_REMEDY)
    return None
