"""Human-readable summary of an analyzer JSON report."""


def print_summary(result, verbose=False, file=None):
    s = result["summary"]
    print(
        "reachable %d / defined %d  (%d indirect-only, %d unreachable)  [backend=%s]"
        % (
            s["reachable"],
            s["defined"],
            s["indirect_only"],
            s["unreachable"],
            result["backend"],
        ),
        file=file,
    )
    if verbose:
        io = [f for f in result["reachable"] if f.get("indirect_only")]
        if io:
            print("  indirect-only (over-approximation surface):", file=file)
            for f in sorted(io, key=lambda x: x["demangled"]):
                print(f"    {f['demangled']}", file=file)
