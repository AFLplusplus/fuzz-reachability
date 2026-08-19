"""Human-readable summary of an analyzer JSON report."""


def print_summary(result):
    s = result["summary"]
    print(
        "reachable %d / defined %d  (%d indirect-only, %d low-confidence, %d unreachable)"
        "  [backend=%s]"
        % (
            s["reachable"],
            s["defined"],
            s["indirect_only"],
            s.get("low_confidence", 0),
            s["unreachable"],
            result["backend"],
        ),
    )


def external_advisory(result):
    s = result["summary"]
    ext = s.get("external_declarations", 0)
    reachable = s.get("reachable", 0)
    if reachable and ext > reachable // 2:
        return ("note: %d external callees are reachable but have no bitcode "
                "body (system libc, precompiled libraries, Rust std without "
                "--build-std, or asm units). The allowlist (reached.txt) cannot "
                "instrument them; prefer the ignorelist (not_reached.txt). "
                "--build-std / --static-libs recover only externals that were "
                "themselves compiled to bitcode; system libraries and asm remain "
                "inherent limits." % ext)
    return None


def optimization_advisory(result, lang_mode=None, optimize=False):
    if optimize:
        return None
    s = result["summary"]
    units = s.get("compile_units", 0)
    optimized = s.get("optimized_compile_units", 0)
    defined = s.get("defined", 0)
    no_inline = s.get("no_inline_definitions", 0)
    if units and optimized:
        evidence = ("%d of %d compile units carry debug info marked isOptimized"
                    % (optimized, units))
    elif (not units and lang_mode in ("c", "cpp") and defined
          and "no_inline_definitions" in s and no_inline * 2 < defined):
        evidence = ("only %d of %d definitions carry noinline/optnone, which this "
                    "tool's own bitcode compile stamps on every one"
                    % (no_inline, defined))
    else:
        return None
    return ("warning: the analyzed bitcode was built with optimization enabled "
            "(%s). Inlining, dead-code elimination and devirtualization delete "
            "functions the source defines, so this function set is post-optimizer: "
            "it cannot be joined name-for-name against source-level tools, and "
            "llvm-cov/cov-analysis rows for the deleted functions match neither "
            "reached.txt nor not_reached.txt. A run that builds the project itself "
            "forces a source-faithful bitcode compile (C/C++ -O0 -fno-inline, Rust "
            "-Copt-level=0), so this bitcode most likely came from a prebuilt "
            "--artifact or a build that recompiled nothing. Rebuild from clean "
            "under this tool (--clean), or pass --optimize to analyze the "
            "post-inline view deliberately." % evidence)
