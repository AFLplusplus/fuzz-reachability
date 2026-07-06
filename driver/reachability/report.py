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
