"""Human-readable summary of an analyzer JSON report."""


def print_summary(result, file=None):
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
