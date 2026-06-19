"""Stage 3/4: invoke the analyzer binary and parse its JSON report."""

import json
import subprocess


class AnalyzeError(RuntimeError):
    pass


def analyze(merged_bc, tc, entries, backend="type-based", dot=None,
            indirect_any=False, reached_out=None, not_reached_out=None):
    """Run the analyzer on `merged_bc`; return the parsed JSON report.

    reached_out / not_reached_out: paths for the sancov allowlist / ignorelist.
    """
    cmd = [tc.analyzer, merged_bc, "--backend", backend]
    for e in entries:
        cmd += ["--entry", e]
    if indirect_any:
        cmd.append("--indirect-any")
    if dot:
        cmd += ["--dot", dot]
    if reached_out:
        cmd += ["--reached-out", reached_out]
    if not_reached_out:
        cmd += ["--not-reached-out", not_reached_out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise AnalyzeError(f"analyzer failed (exit {r.returncode}):\n{r.stderr}")
    return json.loads(r.stdout)
