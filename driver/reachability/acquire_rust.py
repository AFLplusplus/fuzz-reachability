"""Rust bitcode acquisition via rustc --emit=llvm-bc.

With codegen-units=1, rustc emits one .bc per crate into target/<profile>/deps/.
The final link step is expected to fail under --emit=llvm-bc; that is fine -- we
only consume the .bc files, so collection proceeds past a link error. A genuine
*compile* error (a crate or build script that fails to build) is fatal instead,
because the collected .bc set would be silently incomplete and yield a misleading
reachable set; it is detected and raised.

The .bc set is taken from the crates this build actually produced (parsed from
cargo's --message-format=json artifact stream), not from a blind glob of
target/<profile>/deps/*.bc. A glob also picks up stale .bc left by earlier builds
-- cargo never deletes old artifacts -- and linking several .bc of the same crate
fails with "symbol multiply defined". Restricting to this build's artifacts gives
one .bc per crate while preserving genuinely distinct crate versions.

RUSTFLAGS is composed so the project's own flags survive: rustc's bitcode-emit
flags are merged with the caller's RUSTFLAGS / CARGO_ENCODED_RUSTFLAGS, or with
the project's .cargo/config.toml build.rustflags when neither is set. Setting
RUSTFLAGS naively would override (not merge with) cargo's config, dropping project
flags such as `--cfg tokio_unstable` and breaking the build.
"""

import glob
import json
import os
import re
import shlex
import subprocess
import tomllib


class AcquireError(RuntimeError):
    pass


_HASH_RE = re.compile(r"-([0-9a-f]{16})\.")
_BASE_RE = re.compile(r"-[0-9a-f]{16}\.bc$")
_COMPILE_ERROR_MARKERS = (
    "error[E",
    "error: failed to run custom build command",
    "error: cannot find",
    "error: unresolved import",
    "error: cannot determine",
    "error: could not find",
)


def _emit_flags(build_std: bool):
    flags = ["--emit=llvm-bc", "-Cembed-bitcode=yes", "-Ccodegen-units=1"]
    if build_std:
        flags.append("-Zbuild-std")
    return flags


def _rustflags(build_std: bool) -> str:
    return " ".join(_emit_flags(build_std))


def _read_config_rustflags(path):
    """build.rustflags from a cargo config file (array, or whitespace-split
    string), or None when the file is absent / defines no such key."""
    try:
        with open(path, "rb") as fh:
            cfg = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    build = cfg.get("build")
    if not isinstance(build, dict) or "rustflags" not in build:
        return None
    rf = build["rustflags"]
    if isinstance(rf, str):
        return shlex.split(rf)
    if isinstance(rf, list):
        return [str(x) for x in rf]
    return None


def _config_rustflags(project_dir):
    """The build.rustflags cargo would apply for a build in project_dir: the
    closest .cargo/config(.toml) walking up to the filesystem root, else the one
    in $CARGO_HOME / ~/.cargo. Empty list when none defines build.rustflags."""
    d = os.path.abspath(project_dir)
    while True:
        for name in ("config.toml", "config"):
            rf = _read_config_rustflags(os.path.join(d, ".cargo", name))
            if rf is not None:
                return rf
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    home = os.environ.get("CARGO_HOME") or os.path.join(os.path.expanduser("~"), ".cargo")
    for name in ("config.toml", "config"):
        rf = _read_config_rustflags(os.path.join(home, name))
        if rf is not None:
            return rf
    return []


def _compose_rustflags(project_dir, build_std):
    """The full rustc flag list: our emit flags plus whatever flags cargo would
    otherwise apply -- the caller's CARGO_ENCODED_RUSTFLAGS / RUSTFLAGS, or the
    project's config build.rustflags when the environment sets neither."""
    enc = os.environ.get("CARGO_ENCODED_RUSTFLAGS")
    env_rf = os.environ.get("RUSTFLAGS")
    if enc:
        existing = [a for a in enc.split("\x1f") if a]
    elif env_rf and env_rf.strip():
        existing = shlex.split(env_rf)
    else:
        existing = _config_rustflags(project_dir)
    return _emit_flags(build_std) + existing


def _build_env(project_dir, build_std):
    """Build environment with the composed flags in CARGO_ENCODED_RUSTFLAGS, which
    cargo honours verbatim and in preference to both RUSTFLAGS and config."""
    env = dict(os.environ)
    env["CARGO_ENCODED_RUSTFLAGS"] = "\x1f".join(_compose_rustflags(project_dir, build_std))
    env.pop("RUSTFLAGS", None)
    return env


def _compile_errors(text):
    """Lines indicating a genuine compile failure -- as opposed to the expected
    final-link failure under --emit=llvm-bc (`error: linking with ...`)."""
    return [s for s in (ln.strip() for ln in text.splitlines())
            if any(s.startswith(m) for m in _COMPILE_ERROR_MARKERS)]


def _build_bc_paths(stdout):
    """The .bc files this build produced, from cargo's json compiler-artifact
    messages: each carries the build hash in its output filenames, and the
    matching deps/*-<hash>.bc lives beside them. One .bc per built crate; stale
    .bc from other builds (different or absent hash) are not included."""
    bcs = set()
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("reason") != "compiler-artifact":
            continue
        files = list(msg.get("filenames") or [])
        if msg.get("executable"):
            files.append(msg["executable"])
        for f in files:
            m = _HASH_RE.search(os.path.basename(f))
            if m:
                bcs.update(glob.glob(os.path.join(os.path.dirname(f), f"*-{m.group(1)}.bc")))
    return sorted(bcs)


def _dedup_newest_per_crate(paths):
    """One .bc per crate (filename with the trailing -<hash> stripped), keeping
    the most recently modified. Fallback when no artifact stream is available."""
    best = {}
    for p in paths:
        base = _BASE_RE.sub("", os.path.basename(p))
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            continue
        if base not in best or mtime > best[base][0]:
            best[base] = (mtime, p)
    return sorted(p for _, p in best.values())


def acquire_rust_bitcode(project_dir, profile="debug", build_std=False,
                         verbose=False):
    """Build the Rust project and collect every .bc this build emitted.

    verbose: echo the cargo command and pass its diagnostics through (the
    artifact stream on stdout must be captured to be parsed, so the build cannot
    stream live; its rendered diagnostics on stderr are reprinted afterwards).

    Returns a list of .bc paths. Raises AcquireError on a compile failure or when
    no .bc were produced.
    """
    env = _build_env(project_dir, build_std)
    cmd = ["cargo", "build", "--message-format=json-render-diagnostics"]
    if profile == "release":
        cmd.append("--release")
    if build_std:
        cmd += ["-Zbuild-std", "--target", "x86_64-unknown-linux-gnu"]
    if verbose:
        print("  " + " ".join(cmd))
    r = subprocess.run(cmd, cwd=project_dir, env=env, capture_output=True, text=True)
    if verbose and r.stderr.strip():
        print(r.stderr.strip())

    errs = _compile_errors(r.stderr)
    if errs:
        raise AcquireError(
            "cargo failed to compile a crate, so the bitcode set would be "
            "incomplete -- fix the build first:\n  " + "\n  ".join(errs[:20]))

    bcs = _build_bc_paths(r.stdout)
    if not bcs:
        patterns = [os.path.join(project_dir, "target", profile, "deps", "*.bc")]
        if build_std:
            patterns.append(os.path.join(project_dir, "target", "*", profile, "deps", "*.bc"))
        globbed = []
        for pat in patterns:
            globbed.extend(glob.glob(pat))
        bcs = _dedup_newest_per_crate(globbed)
        if bcs:
            print(f"warning: cargo emitted no artifact stream; "
                  f"deduplicated {len(globbed)} .bc in deps/ to {len(bcs)} (one per crate)")
    if not bcs:
        raise AcquireError(f"no .bc produced under {project_dir}/target/{profile}/deps/")
    print(f"rust bitcode: {len(bcs)} crate modules")
    return bcs
