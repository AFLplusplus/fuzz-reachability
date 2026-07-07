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
