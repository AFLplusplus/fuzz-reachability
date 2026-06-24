"""Stage 2: merge collected .bc into one module via llvm-link."""

import hashlib
import os
import subprocess


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
        except OSError:
            out.append(path)
            continue
        key = (size, digest)
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


def link_bitcode(bc_paths, out_path, tc):
    """llvm-link all `bc_paths` into `out_path`. Returns out_path."""
    if not bc_paths:
        raise LinkError("no bitcode files to link")
    bc_paths = _dedup_identical(bc_paths)
    cmd = [tc.llvm_link, *bc_paths, "-o", out_path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise LinkError(f"llvm-link failed:\n{r.stderr}")
    return out_path
