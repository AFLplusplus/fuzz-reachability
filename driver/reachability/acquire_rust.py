"""Rust bitcode acquisition via rustc --emit=llvm-bc.

rustc emits bitcode into target/<profile>/deps/: one .bc per crate at
codegen-units=1, or several (deps/<crate>-<hash>.<cgu>.rcgu.bc) when the build
splits a crate across codegen units. Collection handles both. profile and
codegen_units should mirror the fuzz binary's build: opt level governs generic
sharing and codegen-units governs inlining, both of which decide which
monomorphizations are emitted -- a mismatch yields a reachable set that does not
line up with the instrumented binary. codegen_units defaults to the project's
Cargo.toml [profile.<name>] value, else cargo's per-profile default (dev 256,
release 16); cargo has no manifest default profile, so profile defaults to debug.
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

import atexit
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import tomllib


class AcquireError(RuntimeError):
    pass


_HASH_RE = re.compile(r"-([0-9a-f]{16})\.")
_BASE_RE = re.compile(r"-[0-9a-f]{16}.*\.bc$")
def _build_looks_cached(output):
    """True when the build tool reported it (re)compiled nothing, so its
    artifacts/bitcode reflect an earlier compile rather than this run."""
    t = output or ""
    if any(m in t for m in ("Nothing to be done", " is up to date",
                            "ninja: no work to do", "Nothing to do")):
        return True
    return "Finished" in t and "Compiling " not in t


def _emit_flags(codegen_units: int = 1):
    return ["--emit=llvm-bc", "-Cembed-bitcode=yes",
            f"-Ccodegen-units={codegen_units}"]


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


_PROFILE_SECTION = {"debug": "dev", "release": "release"}
_CARGO_DEFAULT_CGU = {"debug": 256, "release": 16}


def _manifest_codegen_units(project_dir, profile):
    """codegen-units for `profile` from the nearest Cargo.toml up from
    project_dir that sets [profile.<name>] codegen-units (cargo honours the
    workspace-root manifest's profiles, which is found on the way up), or None
    when no manifest in the chain sets it."""
    name = _PROFILE_SECTION.get(profile, profile)
    d = os.path.abspath(project_dir)
    while True:
        try:
            with open(os.path.join(d, "Cargo.toml"), "rb") as fh:
                cfg = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError):
            cfg = None
        if cfg:
            section = cfg.get("profile", {}).get(name) if isinstance(
                cfg.get("profile"), dict) else None
            if isinstance(section, dict) and "codegen-units" in section:
                try:
                    return int(section["codegen-units"])
                except (TypeError, ValueError):
                    return None
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _resolve_codegen_units(project_dir, profile, codegen_units):
    """An explicit codegen_units wins; otherwise the project's Cargo.toml profile
    value, else cargo's documented per-profile default (dev 256, release 16)."""
    if codegen_units is not None:
        return codegen_units
    found = _manifest_codegen_units(project_dir, profile)
    if found is not None:
        return found
    return _CARGO_DEFAULT_CGU.get(profile, 16)


def _manifest_profile_bool(project_dir, profile, key):
    """The bool at [profile.<name>].<key> from the nearest Cargo.toml up from
    project_dir that sets it, or None when none in the chain does."""
    name = _PROFILE_SECTION.get(profile, profile)
    d = os.path.abspath(project_dir)
    while True:
        try:
            with open(os.path.join(d, "Cargo.toml"), "rb") as fh:
                cfg = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError):
            cfg = None
        if cfg:
            section = cfg.get("profile", {}).get(name) if isinstance(
                cfg.get("profile"), dict) else None
            if isinstance(section, dict) and key in section:
                v = section[key]
                return v if isinstance(v, bool) else None
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _resolve_assertions(project_dir, profile):
    """(debug_assertions, overflow_checks) matching cargo for `profile`: the
    manifest [profile.<name>] values when set, else cargo's defaults (release
    off, dev/other on); overflow-checks defaults to debug-assertions. Pinning
    these keeps the source-faithful -Copt-level=0 build -- which would otherwise
    derive debug-assertions=on from opt0 -- consistent with the real profile."""
    name = _PROFILE_SECTION.get(profile, profile)
    da = _manifest_profile_bool(project_dir, profile, "debug-assertions")
    if da is None:
        da = name != "release"
    oc = _manifest_profile_bool(project_dir, profile, "overflow-checks")
    if oc is None:
        oc = da
    return da, oc


def _compose_rustflags(project_dir, codegen_units=1, optimize=False,
                       profile="debug", mangling="auto"):
    """The full rustc flag list: our emit flags plus whatever flags cargo would
    otherwise apply -- the caller's CARGO_ENCODED_RUSTFLAGS / RUSTFLAGS, or the
    project's config build.rustflags when the environment sets neither. When not
    optimize, -Copt-level=0 is appended (after the inherited flags, so it wins
    over the profile) together with -Cdebug-assertions/-Coverflow-checks pinned
    to the profile's values, so forcing opt0 does not silently flip those cfgs
    (which would change which functions compile), giving source-faithful
    (un-inlined) bitcode that still matches the real profile. mangling
    ("auto"/"legacy"/"v0") appends -Csymbol-mangling-version=<mangling> so the
    analysis bitcode's Rust symbols match a target built with that scheme (e.g.
    a -Cinstrument-coverage build, which is v0); "auto" appends nothing, leaving
    rustc's own default (legacy on stable toolchains)."""
    enc = os.environ.get("CARGO_ENCODED_RUSTFLAGS")
    env_rf = os.environ.get("RUSTFLAGS")
    if enc:
        existing = [a for a in enc.split("\x1f") if a]
    elif env_rf and env_rf.strip():
        existing = shlex.split(env_rf)
    else:
        existing = _config_rustflags(project_dir)
    flags = _emit_flags(codegen_units) + existing
    if not optimize:
        da, oc = _resolve_assertions(project_dir, profile)
        flags = flags + [
            "-Copt-level=0",
            f"-Cdebug-assertions={'on' if da else 'off'}",
            f"-Coverflow-checks={'on' if oc else 'off'}",
        ]
    if mangling != "auto":
        flags = flags + [f"-Csymbol-mangling-version={mangling}"]
    return flags


def _build_env(project_dir, codegen_units=1, optimize=False, profile="debug",
               mangling="auto"):
    """Build environment with the composed flags in CARGO_ENCODED_RUSTFLAGS, which
    cargo honours verbatim and in preference to both RUSTFLAGS and config."""
    env = dict(os.environ)
    env["CARGO_ENCODED_RUSTFLAGS"] = "\x1f".join(
        _compose_rustflags(project_dir, codegen_units, optimize, profile, mangling))
    env.pop("RUSTFLAGS", None)
    return env


def _compile_errors(text):
    """Lines indicating a genuine compile failure -- as opposed to the expected
    final-link failure under --emit=llvm-bc (`error: linking with ...`)."""
    errors = []
    for s in (ln.strip() for ln in text.splitlines()):
        if not s.startswith("error"):
            continue
        if s.startswith("error: linking with "):
            continue
        if s.startswith("error: could not compile "):
            continue
        if s.startswith("error: aborting due to "):
            continue
        errors.append(s)
    return errors


def _rustc_host():
    r = subprocess.run(["rustc", "-vV"], capture_output=True, text=True)
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            if line.startswith("host: "):
                return line[6:].strip()
    raise AcquireError("cannot determine rustc host target from `rustc -vV`")


_NAMED_KINDS = ("bin", "cdylib", "staticlib", "dylib")


def _named_bc_paths(msg, files):
    """The .bc files for a link-product artifact (bin/cdylib/staticlib/dylib),
    whose cargo message carries only the bare or uplifted output path
    (target/<profile>/<name>, no build hash) -- so the hash-based collection
    below cannot match it and the crate body would be dropped. rustc emits the
    unit's bitcode as deps/<name with '-' -> '_'>-<hash>.bc (or several
    .<cgu>.rcgu.bc under codegen-units > 1); take every .bc of the newest build's
    hash (a rebuild rewrites them, so newest is this build's). Returns [] for
    other targets or when no matching .bc exists."""
    kind = msg.get("target", {}).get("kind") or []
    if not any(k in _NAMED_KINDS for k in kind):
        return []
    name = msg.get("target", {}).get("name")
    if not name or not files:
        return []
    deps = os.path.join(os.path.dirname(files[0]), "deps")
    stem = name.replace("-", "_")
    cands = [p for p in glob.glob(os.path.join(deps, f"{stem}-*.bc"))
             if _BASE_RE.sub("", os.path.basename(p)) == stem]
    if not cands:
        return []
    hashes = {mm.group(1) for p in cands
              if (mm := _HASH_RE.search(os.path.basename(p)))}
    if len(hashes) > 1:
        print(f"warning: deps/ holds bitcode from {len(hashes)} builds of crate "
              f"'{stem}'; picking the newest by mtime, which can be stale if this "
              f"crate was cached. Re-run with --clean for a fresh build.")
    newest = max(cands, key=os.path.getmtime)
    m = _HASH_RE.search(os.path.basename(newest))
    if not m:
        return [newest]
    return sorted(p for p in cands if f"-{m.group(1)}." in os.path.basename(p))


def _build_bc_paths(stdout):
    """The .bc files this build produced, from cargo's json compiler-artifact
    messages: a library (rlib) unit carries the build hash in its output
    filenames, and the matching deps/*-<hash>.bc lives beside them. A
    bin/staticlib/cdylib/dylib unit carries only the bare or uplifted output path
    (no hash), so its bitcode is resolved by name via _named_bc_paths. One or more
    .bc per built crate; stale .bc from other builds (different or absent hash)
    are not included."""
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
        matched = False
        for f in files:
            m = _HASH_RE.search(os.path.basename(f))
            if m:
                bcs.update(glob.glob(os.path.join(os.path.dirname(f), f"*-{m.group(1)}*.bc")))
                matched = True
        if not matched:
            bcs.update(_named_bc_paths(msg, files))
    return sorted(bcs)


def _dedup_newest_per_crate(paths):
    """The newest build's .bc for each crate, keeping every codegen unit of that
    build. Fallback when no artifact stream is available: a glob of deps/*.bc can
    mix several builds (cargo never deletes old artifacts) and, at codegen-units >
    1, several .bc per build. Group by crate (filename with the trailing -<hash>...
    stripped), take the most recently modified file in each group as this build,
    and keep all of that group's files sharing its build hash."""
    groups = {}
    for p in paths:
        base = _BASE_RE.sub("", os.path.basename(p))
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            continue
        groups.setdefault(base, []).append((mtime, p))
    kept = []
    for items in groups.values():
        _, newest = max(items)
        m = _HASH_RE.search(os.path.basename(newest))
        if not m:
            kept.append(newest)
            continue
        tag = f"-{m.group(1)}."
        kept.extend(p for _, p in items if tag in os.path.basename(p))
    return sorted(kept)


def acquire_rust_bitcode(project_dir, profile="debug", build_std=False,
                         codegen_units=None, verbose=False, optimize=False,
                         mangling="auto"):
    """Build the Rust project and collect every .bc this build emitted.

    profile / codegen_units should match the fuzz binary's build so the emitted
    monomorphizations line up with the instrumented binary. codegen_units=None
    (the default) resolves to the project's Cargo.toml [profile.<name>]
    codegen-units, or cargo's per-profile default when the manifest is silent.

    verbose: echo the cargo command and pass its diagnostics through (the
    artifact stream on stdout must be captured to be parsed, so the build cannot
    stream live; its rendered diagnostics on stderr are reprinted afterwards).

    optimize: when False (default) force -Copt-level=0 so functions are not
    inlined away (source-faithful, matches llvm-cov, safe allowlist superset);
    when True analyze at the profile's real optimization to mirror the binary.

    mangling: "auto" (default, rustc's own default), "legacy", or "v0" --
    force the analysis build's Rust symbol-mangling scheme to match the target
    it will be joined against (see _compose_rustflags).

    Returns a list of .bc paths. Raises AcquireError on a compile failure or when
    no .bc were produced.
    """
    codegen_units = _resolve_codegen_units(project_dir, profile, codegen_units)
    env = _build_env(project_dir, codegen_units, optimize=optimize,
                     profile=profile, mangling=mangling)
    cmd = ["cargo", "build", "--message-format=json-render-diagnostics"]
    if profile == "release":
        cmd.append("--release")
    if build_std:
        cmd += ["-Zbuild-std", "--target", _rustc_host()]
    if verbose:
        print(f"  profile={profile}, codegen-units={codegen_units}")
        print("  " + " ".join(cmd))
    r = subprocess.run(cmd, cwd=project_dir, env=env, capture_output=True, text=True)
    if verbose and r.stderr.strip():
        print(r.stderr.strip())

    errs = _compile_errors(r.stderr)
    if errs:
        raise AcquireError(
            "cargo failed to compile a crate, so the bitcode set would be "
            "incomplete -- fix the build first:\n  " + "\n  ".join(errs[:20]))
    if r.returncode != 0 and "error: linking with " not in r.stderr:
        tail = (r.stderr.strip() or r.stdout.strip())[-2000:]
        raise AcquireError(f"cargo build failed (exit {r.returncode}):\n  {tail}")

    if _build_looks_cached(r.stderr):
        print("warning: cargo recompiled nothing -- this build is CACHED, so the "
              "bitcode is from an earlier compile and may be stale. Run "
              "`cargo clean` and re-run for a fresh build.")

    bcs = _build_bc_paths(r.stdout)
    if not bcs and r.returncode == 0:
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


_BC_WRAPPER = """#!/bin/sh
real="$1"
shift
"$real" "$@" --emit=llvm-bc $REACH_EXTRA_RUSTFLAGS
status=$?
[ -n "$REACH_BC_DIR" ] || exit $status
outdir=""
cname=""
ctype=""
prev=""
for a in "$@"; do
  case "$a" in
    --out-dir=*) outdir="${a#--out-dir=}" ;;
    --crate-name=*) cname="${a#--crate-name=}" ;;
    --crate-type=*) ctype="${a#--crate-type=}" ;;
  esac
  case "$prev" in
    --out-dir) outdir="$a" ;;
    --crate-name) cname="$a" ;;
    --crate-type) ctype="$a" ;;
  esac
  prev="$a"
done
[ -n "$outdir" ] && [ -n "$cname" ] || exit $status
case "$cname" in build_script_*) exit $status ;; esac
case "$ctype" in *proc-macro*) exit $status ;; esac
for f in "$outdir/$cname"-*.bc; do
  [ -e "$f" ] && cp -f "$f" "$REACH_BC_DIR/" 2>/dev/null
done
exit $status
"""


def acquire_rust_bitcode_native(project_dir, build_cmd, shell=False, verbose=False,
                                optimize=False, mangling="auto"):
    """Build via the fuzzer's own command (cargo afl/ziggy/fuzz, or a custom
    build_cmd) and collect the bitcode it emits. A RUSTC_WRAPPER adds
    --emit=llvm-bc to every crate rustc compiles and copies that crate's .bc into
    a private directory, so the harness keeps the cfgs and flags its real build
    sets (cfg(fuzzing), opt level, instrumentation) -- which a plain `cargo build`
    would miss -- and collection is independent of where the tool writes output.

    AFLRS_REQUIRE_PLUGINS=1 is set so cargo-afl / ziggy fail loudly when the AFL++
    LLVM plugins are absent instead of silently building with weaker
    instrumentation that would not match the real fuzzer.

    build_cmd is an argv list, or a shell string when shell=True. Returns the
    collected .bc paths. Raises AcquireError on a build failure, or when nothing
    was captured (a fully cached build never re-runs rustc).

    optimize: when False (default) the wrapper also forces -Copt-level=0 so the
    emitted bitcode is source-faithful (functions not inlined away); the caller is
    responsible for cleaning the resulting throwaway opt-0 build. When True the
    build mirrors the real instrumented binary.

    mangling: "auto" (default, the harness's own scheme -- legacy for
    cargo-afl/ziggy/cargo-fuzz), "legacy", or "v0" -- forces the wrapper's
    -Csymbol-mangling-version to match the target being joined against."""
    collect = tempfile.mkdtemp(prefix="reach-bc-")
    atexit.register(shutil.rmtree, collect, ignore_errors=True)
    wrapper = os.path.join(collect, ".rustc-bc-wrapper.sh")
    with open(wrapper, "w") as fh:
        fh.write(_BC_WRAPPER)
    os.chmod(wrapper, 0o755)

    extra = [] if optimize else ["-Copt-level=0"]
    if mangling != "auto":
        extra.append(f"-Csymbol-mangling-version={mangling}")

    env = dict(os.environ)
    env["RUSTC_WRAPPER"] = wrapper
    env["REACH_BC_DIR"] = collect
    env["REACH_EXTRA_RUSTFLAGS"] = " ".join(extra)
    env.setdefault("AFLRS_REQUIRE_PLUGINS", "1")

    argv = ["sh", "-c", build_cmd] if shell else list(build_cmd)
    if verbose:
        print("  native build: " + (build_cmd if shell else " ".join(argv)))
    r = subprocess.run(argv, cwd=project_dir, env=env, capture_output=True, text=True)
    if verbose and r.stderr.strip():
        print(r.stderr.strip())
    if r.returncode != 0:
        tail = (r.stderr.strip() or r.stdout.strip())[-2000:]
        hint = ""
        if "plugin" in tail.lower():
            hint = ("\n  AFL++ LLVM plugins are required (AFLRS_REQUIRE_PLUGINS=1); "
                    "build them with `cargo afl config --build --plugins --force`.")
        raise AcquireError("the fuzzer build failed; fix it first:\n  " + tail + hint)

    bcs = sorted(glob.glob(os.path.join(collect, "*.bc")))
    if not bcs:
        why = ("the build was CACHED (the tool recompiled nothing), so rustc "
               "never ran" if _build_looks_cached(r.stderr + "\n" + r.stdout)
               else "no bitcode was captured")
        raise AcquireError(
            why + ". Clean it first (e.g. `cargo clean`, or for cargo-fuzz remove "
            "fuzz/target) and re-run so every crate compiles under the wrapper.")
    print(f"rust bitcode (native build): {len(bcs)} crate modules")
    return bcs
