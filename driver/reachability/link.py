"""Stage 2: merge collected .bc into one module via llvm-link."""

import subprocess


class LinkError(RuntimeError):
    pass


def link_bitcode(bc_paths, out_path, tc):
    """llvm-link all `bc_paths` into `out_path`. Returns out_path."""
    if not bc_paths:
        raise LinkError("no bitcode files to link")
    cmd = [tc.llvm_link, *bc_paths, "-o", out_path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise LinkError(f"llvm-link failed:\n{r.stderr}")
    return out_path
