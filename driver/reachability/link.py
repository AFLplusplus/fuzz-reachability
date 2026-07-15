"""Stage 2: merge collected .bc into one module via llvm-link."""

import hashlib
import os
import subprocess
import tempfile

from .errors import tail


class LinkError(RuntimeError):
    pass


def _dedup_identical(paths):
    seen = set()
    out = []
    for path in paths:
        try:
            size = os.path.getsize(path)
            h = hashlib.sha256()
            with open(path, "rb") as fh:
                while chunk := fh.read(1024 * 1024):
                    h.update(chunk)
            digest = h.digest()
        except OSError as exc:
            raise LinkError(f"cannot read bitcode input {path}: {exc}") from exc
        key = (size, digest)
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


def _run_link(paths, out_path, tc):
    cmd = [tc.llvm_link, *paths, "-o", out_path]
    try:
        r = subprocess.run(cmd, capture_output=True)
    except OSError as exc:
        raise LinkError(f"cannot run llvm-link {tc.llvm_link}: {exc}") from exc
    if r.returncode != 0:
        raise LinkError(
            f"llvm-link failed (exit {r.returncode}):\n{tail(r.stderr)}"
        )


def link_bitcode(bc_paths, out_path, tc, batch_size=200):
    """llvm-link all `bc_paths` into `out_path`. Returns out_path."""
    if not bc_paths:
        raise LinkError("no bitcode files to link")
    bc_paths = _dedup_identical(bc_paths)
    if batch_size < 2:
        raise LinkError("llvm-link batch size must be at least 2")
    try:
        with tempfile.TemporaryDirectory(
            prefix="reach-link-", dir=os.path.dirname(os.path.abspath(out_path))
        ) as directory:
            round_paths = list(bc_paths)
            round_number = 0
            while len(round_paths) > batch_size:
                next_paths = []
                for index in range(0, len(round_paths), batch_size):
                    part = os.path.join(
                        directory, f"round-{round_number}-{index // batch_size}.bc"
                    )
                    _run_link(round_paths[index:index + batch_size], part, tc)
                    next_paths.append(part)
                round_paths = next_paths
                round_number += 1
            _run_link(round_paths, out_path, tc)
    except OSError as exc:
        raise LinkError(f"cannot create llvm-link intermediates: {exc}") from exc
    return out_path
