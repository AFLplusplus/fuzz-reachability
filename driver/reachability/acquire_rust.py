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
fails with "symbol multiply defined". Restricting to this build's artifacts
preserves every current codegen unit and genuinely distinct crate version.

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
import time
import tomllib

from .errors import build_looks_cached, decode, tail


class AcquireError(RuntimeError):
    pass


_HASH_RE = re.compile(r"-([0-9a-f]{16})\.")
_BASE_RE = re.compile(r"-[0-9a-f]{16}.*\.bc$")
_build_looks_cached = build_looks_cached


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
    hierarchical merge from $CARGO_HOME through ancestor .cargo/config files.
    Empty list when none defines build.rustflags."""
    directories = []
    d = os.path.abspath(project_dir)
    while True:
        directories.append(d)
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    home = os.environ.get("CARGO_HOME") or os.path.join(os.path.expanduser("~"), ".cargo")
    config_dirs = [home] + [
        os.path.join(directory, ".cargo") for directory in reversed(directories)
    ]
    merged = []
    seen = set()
    for config_dir in config_dirs:
        identity = os.path.realpath(config_dir)
        if identity in seen:
            continue
        seen.add(identity)
        for name in ("config.toml", "config"):
            rf = _read_config_rustflags(os.path.join(config_dir, name))
            if rf is not None:
                merged.extend(rf)
                break
    return merged


_PROFILE_SECTION = {"debug": "dev", "release": "release"}
_CARGO_DEFAULT_CGU = {"debug": 256, "release": 16}


def _cargo_metadata(project_dir):
    cmd = ["cargo", "metadata", "--no-deps", "--format-version", "1"]
    try:
        result = subprocess.run(cmd, cwd=project_dir, capture_output=True)
    except OSError as exc:
        raise AcquireError(f"cannot run cargo metadata: {exc}") from exc
    if result.returncode != 0:
        raise AcquireError(
            f"cargo metadata failed (exit {result.returncode}):\n{tail(result.stderr)}"
        )
    try:
        return json.loads(decode(result.stdout))
    except json.JSONDecodeError as exc:
        raise AcquireError(f"cargo metadata returned malformed JSON: {exc}") from exc


def _profile_manifest(project_dir):
    directory = os.path.realpath(project_dir)
    fallback = None
    workspace_fallback = None
    current = directory
    while True:
        candidate = os.path.join(current, "Cargo.toml")
        if os.path.isfile(candidate):
            if fallback is None:
                fallback = candidate
            try:
                with open(candidate, "rb") as fh:
                    config = tomllib.load(fh)
            except (OSError, tomllib.TOMLDecodeError):
                config = {}
            if isinstance(config.get("workspace"), dict):
                workspace_fallback = candidate
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    if fallback is None:
        return None
    try:
        metadata = _cargo_metadata(project_dir)
    except AcquireError:
        return workspace_fallback or fallback
    workspace_root = os.path.realpath(metadata.get("workspace_root", ""))
    members = set(metadata.get("workspace_members") or [])
    candidates = []
    for package in metadata.get("packages") or []:
        manifest = os.path.realpath(package.get("manifest_path", ""))
        package_dir = os.path.dirname(manifest)
        try:
            inside = os.path.commonpath([directory, package_dir]) == package_dir
        except ValueError:
            inside = False
        if inside:
            candidates.append((len(package_dir), package, manifest))
    if candidates:
        _, package, manifest = max(candidates)
        if package.get("id") in members and workspace_root:
            return os.path.join(workspace_root, "Cargo.toml")
        return manifest
    manifest = os.path.join(directory, "Cargo.toml")
    if workspace_root:
        return os.path.join(workspace_root, "Cargo.toml")
    return manifest


def _profile_section(project_dir, profile):
    name = _PROFILE_SECTION.get(profile, profile)
    manifest = _profile_manifest(project_dir)
    if manifest is None:
        return {}
    try:
        with open(manifest, "rb") as fh:
            cfg = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise AcquireError(f"cannot read Cargo profile configuration: {exc}") from exc
    profiles = cfg.get("profile")
    section = profiles.get(name) if isinstance(profiles, dict) else None
    return section if isinstance(section, dict) else {}


def _manifest_codegen_units(project_dir, profile):
    """codegen-units for `profile` using Cargo workspace profile semantics."""
    section = _profile_section(project_dir, profile)
    if "codegen-units" not in section:
        return None
    try:
        return int(section["codegen-units"])
    except (TypeError, ValueError):
        return None


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
    """The bool at [profile.<name>].<key> using Cargo workspace semantics."""
    value = _profile_section(project_dir, profile).get(key)
    return value if isinstance(value, bool) else None


def _resolve_assertions(project_dir, profile):
    """(debug_assertions, overflow_checks) matching cargo for `profile`: the
    manifest [profile.<name>] values when set, else cargo's defaults (release
    off, dev/other on); overflow-checks defaults to debug-assertions. Pinning
    these keeps the source-faithful -Copt-level=0 build -- which would otherwise
    derive debug-assertions=on from opt0 -- consistent with the real profile."""
    name = _PROFILE_SECTION.get(profile, profile)
    section = _profile_section(project_dir, profile)
    value = section.get("debug-assertions")
    da = value if isinstance(value, bool) else None
    if da is None:
        da = name != "release"
    value = section.get("overflow-checks")
    oc = value if isinstance(value, bool) else None
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
    try:
        r = subprocess.run(["rustc", "-vV"], capture_output=True)
    except OSError as exc:
        raise AcquireError(f"cannot run rustc -vV: {exc}") from exc
    if r.returncode == 0:
        for line in decode(r.stdout).splitlines():
            if line.startswith("host: "):
                return line[6:].strip()
    raise AcquireError("cannot determine rustc host target from `rustc -vV`")


_NAMED_KINDS = ("bin", "cdylib", "staticlib", "dylib")


def _named_bc_paths(msg, files, newer_than=None):
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
    cands = [
        p for p in glob.glob(os.path.join(deps, f"{stem}-*.bc"))
        if _BASE_RE.sub("", os.path.basename(p)) == stem
        and (newer_than is None or os.path.getmtime(p) >= newer_than - 2)
    ]
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


def _build_bc_paths_lines(lines, newer_than=None):
    """The .bc files this build produced, from cargo's json compiler-artifact
    messages: a library (rlib) unit carries the build hash in its output
    filenames, and the matching deps/*-<hash>.bc lives beside them. A
    bin/staticlib/cdylib/dylib unit carries only the bare or uplifted output path
    (no hash), so its bitcode is resolved by name via _named_bc_paths. One or more
    .bc per built crate; stale .bc from other builds (different or absent hash)
    are not included."""
    bcs = set()
    for line in lines:
        line = decode(line).strip()
        if not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("reason") != "compiler-artifact":
            continue
        target = msg.get("target") or {}
        kinds = set(target.get("kind") or [])
        name = target.get("name") or ""
        if "custom-build" in kinds or "proc-macro" in kinds or name.startswith("build_script_"):
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
            bcs.update(_named_bc_paths(msg, files, newer_than=newer_than))
    if newer_than is not None:
        bcs = {path for path in bcs if os.path.getmtime(path) >= newer_than - 2}
    return sorted(bcs)


def _build_bc_paths(stdout, newer_than=None):
    return _build_bc_paths_lines(decode(stdout).splitlines(), newer_than=newer_than)


def _file_tail(stream, limit=1024 * 1024):
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(max(0, size - limit))
    return decode(stream.read())


def _dedup_newest_per_crate(paths, newer_than=None):
    """Keep every unique invocation-fresh fallback bitcode path."""
    kept = []
    seen = set()
    for p in paths:
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            continue
        if newer_than is not None and mtime < newer_than - 2:
            continue
        identity = os.path.realpath(p)
        if identity in seen:
            continue
        seen.add(identity)
        kept.append(p)
    return sorted(kept)


def _target_dir(project_dir):
    configured = os.environ.get("CARGO_TARGET_DIR")
    if not configured:
        return os.path.join(project_dir, "target")
    if os.path.isabs(configured):
        return configured
    return os.path.abspath(os.path.join(project_dir, configured))


def _validate_bitcode_paths(paths):
    for path in paths:
        try:
            with open(path, "rb") as fh:
                fh.read(1)
        except OSError as exc:
            raise AcquireError(f"cannot read Rust bitcode {path}: {exc}") from exc
    return paths


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
    started = time.time()
    try:
        with tempfile.TemporaryFile() as stdout_log, tempfile.TemporaryFile() as stderr_log:
            r = subprocess.run(
                cmd, cwd=project_dir, env=env, stdout=stdout_log, stderr=stderr_log,
            )
            mocked_stdout = getattr(r, "stdout", None)
            mocked_stderr = getattr(r, "stderr", None)
            if mocked_stdout:
                stdout_log.write(
                    mocked_stdout if isinstance(mocked_stdout, bytes)
                    else str(mocked_stdout).encode()
                )
            if mocked_stderr:
                stderr_log.write(
                    mocked_stderr if isinstance(mocked_stderr, bytes)
                    else str(mocked_stderr).encode()
                )
            stdout_log.seek(0)
            bcs = _build_bc_paths_lines(stdout_log, newer_than=started)
            stdout = _file_tail(stdout_log)
            stderr = _file_tail(stderr_log)
    except OSError as exc:
        raise AcquireError(f"cannot run cargo build: {exc}") from exc
    if verbose and stderr.strip():
        print(stderr.strip())

    errs = _compile_errors(stderr)
    if errs:
        raise AcquireError(
            "cargo failed to compile a crate, so the bitcode set would be "
            "incomplete -- fix the build first:\n  " + "\n  ".join(errs[:20]))
    if r.returncode != 0 and "error: linking with " not in stderr:
        detail = tail(stderr or stdout, 2000)
        raise AcquireError(f"cargo build failed (exit {r.returncode}):\n  {detail}")

    if _build_looks_cached(stderr):
        print("warning: cargo recompiled nothing -- this build is CACHED, so the "
              "bitcode is from an earlier compile and may be stale. Run "
              "`cargo clean` and re-run for a fresh build.")

    if not bcs and r.returncode == 0:
        target_dir = _target_dir(project_dir)
        patterns = [os.path.join(target_dir, profile, "deps", "*.bc")]
        if build_std:
            patterns.append(os.path.join(target_dir, "*", profile, "deps", "*.bc"))
        globbed = []
        for pat in patterns:
            globbed.extend(glob.glob(pat))
        bcs = _dedup_newest_per_crate(globbed, newer_than=started)
        if bcs:
            print(f"warning: fallback bitcode collection: cargo emitted no artifact "
                  f"stream; selected {len(bcs)} of {len(globbed)} invocation-fresh "
                  ".bc files")
    if not bcs:
        raise AcquireError(f"no invocation-fresh .bc produced under {_target_dir(project_dir)}")
    _validate_bitcode_paths(bcs)
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
    try:
        with open(wrapper, "w") as fh:
            fh.write(_BC_WRAPPER)
        os.chmod(wrapper, 0o755)
    except OSError as exc:
        raise AcquireError(f"cannot create rustc bitcode wrapper: {exc}") from exc

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
    try:
        r = subprocess.run(argv, cwd=project_dir, env=env, capture_output=True)
    except OSError as exc:
        raise AcquireError(f"cannot run native Rust build: {exc}") from exc
    stdout = decode(r.stdout)
    stderr = decode(r.stderr)
    if verbose and stderr.strip():
        print(stderr.strip())
    if r.returncode != 0:
        detail = tail(stderr or stdout, 2000)
        hint = ""
        if "plugin" in detail.lower():
            hint = ("\n  AFL++ LLVM plugins are required (AFLRS_REQUIRE_PLUGINS=1); "
                    "build them with `cargo afl config --build --plugins --force`.")
        raise AcquireError("the fuzzer build failed; fix it first:\n  " + detail + hint)

    bcs = sorted(glob.glob(os.path.join(collect, "*.bc")))
    if not bcs:
        why = ("the build was CACHED (the tool recompiled nothing), so rustc "
               "never ran" if _build_looks_cached(stderr + "\n" + stdout)
               else "no bitcode was captured")
        raise AcquireError(
            why + ". Clean it first (e.g. `cargo clean`, or for cargo-fuzz remove "
            "fuzz/target) and re-run so every crate compiles under the wrapper.")
    _validate_bitcode_paths(bcs)
    print(f"rust bitcode (native build): {len(bcs)} crate modules")
    return bcs
