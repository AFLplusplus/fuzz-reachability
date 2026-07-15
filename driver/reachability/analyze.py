"""Stage 3/4: invoke the analyzer binary and parse its JSON report."""

import json
import subprocess
import sys

from .errors import decode, tail


class AnalyzeError(RuntimeError):
    pass


def analyze(merged_bc, tc, entries, dot=None,
            reached_out=None, not_reached_out=None, out_path=None, verbose=False,
            include_process_lifecycle_roots=False):
    """Run the analyzer on `merged_bc`; return the parsed JSON report.

    reached_out / not_reached_out: paths for the sancov allowlist / ignorelist.
    verbose: echo the exact analyzer command. Analyzer warnings are always shown.
    """
    cmd = [tc.analyzer, merged_bc]
    for e in entries:
        cmd += ["--entry", e]
    if dot:
        cmd += ["--dot", dot]
    if reached_out:
        cmd += ["--reached-out", reached_out]
    if not_reached_out:
        cmd += ["--not-reached-out", not_reached_out]
    if out_path:
        cmd += ["--out", out_path]
    if include_process_lifecycle_roots:
        cmd.append("--include-process-lifecycle-roots")
    if verbose:
        print("  " + " ".join(cmd))
    try:
        r = subprocess.run(cmd, capture_output=True)
    except OSError as exc:
        raise AnalyzeError(f"cannot run analyzer {tc.analyzer}: {exc}") from exc
    stderr = decode(r.stderr)
    if r.returncode != 0:
        raise AnalyzeError(
            f"analyzer failed (exit {r.returncode}):\n{tail(stderr)}"
        )
    if stderr:
        sys.stderr.write(stderr)
        if not stderr.endswith("\n"):
            sys.stderr.write("\n")
    try:
        if out_path:
            with open(out_path, encoding="utf-8", errors="replace") as fh:
                return json.load(fh)
        return json.loads(decode(r.stdout))
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        raise AnalyzeError(f"cannot read analyzer JSON output: {exc}") from exc
