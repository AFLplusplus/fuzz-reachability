"""C / C++ bitcode acquisition via gllvm.

Build the project with the gclang/gclang++ wrappers (which embed bitcode-path
metadata into each object), then run get-bc on the built artifact to extract a
whole-program .bc. The artifact is auto-detected from the build output when not
given explicitly. Independent of the project's own LTO setup.

When the build command is auto-detected (no explicit --build-cmd), it also forces
a static build wherever the build system allows it, because shared libraries are
built and linked separately and their bitcode never reaches the target. See
detect_build_cmd for the per-build-system flags.

Static-library expansion: a linked binary only embeds the archive members the
linker actually pulled in, so functions in unreferenced members of a static
library would otherwise be invisible to the analysis. With static_libs="auto"
(the default), each static archive the target links is additionally extracted in
full (get-bc -b) and merged with the target's own (non-archive) objects, so every
function in the library is classified reachable/unreachable rather than silently
dropped. "none" keeps only the linker's view; "all" pulls in every bitcode
archive found in the tree, save those whose members are already covered by a
larger one. Merging the full archive with the target's *non-archive* objects (its
manifest minus the archive members) avoids duplicate definitions. Archive manifests
provide exact object provenance; incomplete extraction fails the run.
"""

import atexit
from collections import deque
import hashlib
import mmap
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time

from reachability import diagnostics
from reachability.errors import build_is_cached, build_looks_cached, decode, tail


class AcquireError(RuntimeError):
    pass


_SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv",
              ".cache"}
_BC_MARKERS = (b".llvm_bc", b"__llvm_bc")
_KIND_RANK = {"exec": 3, "shared": 2, "archive": 1, "object": 0}
_MACHO_MAGIC = (0xFEEDFACE, 0xFEEDFACF, 0xCEFAEDFE, 0xCFFAEDFE, 0xBEBAFECA, 0xBFBAFECA)


_build_looks_cached = build_looks_cached


_BITCODE_NO_INLINE_FLAGS = "-fno-inline -fno-inline-functions"


def _build_env(clang_bindir: str, optimize: bool = False) -> dict:
    """Environment for the gllvm build. By default (optimize=False) it asks gllvm
    to emit source-faithful bitcode via LLVM_BITCODE_GENERATION_FLAGS, which gllvm
    applies only to the bitcode compile (never the native object), so functions
    are not inlined away in what the analyzer reads. optimize=True leaves inlining
    to the build's own flags."""
    env = dict(os.environ)
    env["CC"] = "gclang"
    env["CXX"] = "gclang++"
    env["LLVM_COMPILER_PATH"] = clang_bindir
    env["LC_ALL"] = "C"
    if not optimize:
        inherited = env.get("LLVM_BITCODE_GENERATION_FLAGS", "")
        env["LLVM_BITCODE_GENERATION_FLAGS"] = (
            (inherited + " " + _BITCODE_NO_INLINE_FLAGS).strip()
        )
    return env


def _gllvm_env(env, tc, tool_dir=None):
    def resolved(value):
        found = shutil.which(value)
        return os.path.abspath(found or value)

    clang = resolved(tc.clang)
    clangxx = resolved(getattr(tc, "clangxx", clang))
    llvm_link = resolved(getattr(tc, "llvm_link", "llvm-link"))
    if tool_dir:
        try:
            os.makedirs(tool_dir, exist_ok=True)
            for name, target in (
                ("clang", clang), ("clang++", clangxx), ("llvm-link", llvm_link)
            ):
                path = os.path.join(tool_dir, name)
                if os.path.lexists(path):
                    os.unlink(path)
                os.symlink(target, path)
        except OSError as exc:
            raise AcquireError(f"cannot prepare exact gllvm tool directory: {exc}") from exc
        env["LLVM_COMPILER_PATH"] = tool_dir
        env["LLVM_CC_NAME"] = "clang"
        env["LLVM_CXX_NAME"] = "clang++"
        env["LLVM_LINK_NAME"] = "llvm-link"
    else:
        env["LLVM_COMPILER_PATH"] = os.path.dirname(clang)
        env["LLVM_CC_NAME"] = os.path.basename(clang)
        env["LLVM_CXX_NAME"] = os.path.basename(clangxx)
        env["LLVM_LINK_NAME"] = os.path.basename(llvm_link)
    directories = ([tool_dir] if tool_dir else []) + [
        os.path.dirname(clang), os.path.dirname(clangxx),
        os.path.dirname(llvm_link),
    ]
    env["PATH"] = os.pathsep.join(dict.fromkeys(
        [d for d in directories if d] + [env.get("PATH", "")]
    ))
    return env


_AUTOTOOLS_STATIC_FLAGS = ("--disable-shared", "--enable-static")


def _configure_help(project_dir):
    """`./configure --help` text for the project, or "" when it cannot be run.
    Tries to exec the script directly, falling back to `sh configure` only when
    exec fails (e.g. the file is not marked executable) -- never on empty output,
    so a configure that ignores `--help` is not accidentally run a second time."""
    for argv in (["./configure", "--help"], ["sh", "configure", "--help"]):
        try:
            r = subprocess.run(argv, cwd=project_dir, capture_output=True,
                               text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            continue
        return (r.stdout or "") + (r.stderr or "")
    return ""


def _configure_flags(project_dir):
    """Static + LTO options a project's `configure` understands, probed from one
    `configure --help` call. libtool advertises `--enable-shared`/`--enable-static`
    (never the `--disable-` forms), so their presence is what tells us
    `--disable-shared`/`--enable-static` are accepted; likewise an advertised
    `--enable-lto`/`--disable-lto` means `--disable-lto` is accepted. Returns the
    subset to pass, in order; empty when the script has no such knobs."""
    help_text = _configure_help(project_dir)
    flags = []
    if "--enable-shared" in help_text:
        flags.append("--disable-shared")
    if "--enable-static" in help_text:
        flags.append("--enable-static")
    if "--enable-lto" in help_text or "--disable-lto" in help_text:
        flags.append("--disable-lto")
    return flags


def detect_build_cmd(project_dir):
    """Pick a build command for a C/C++ project by probing for the well-known
    build files, in the order configure -> make -> cmake -> ninja -> meson, with
    an autotools-bootstrap fallback. Returns a shell command string, or None if
    nothing is recognized (the caller then falls back to plain `make`). The
    gllvm wrappers are injected via CC/CXX, which every build system below
    honours at configure time, so the chosen command embeds bitcode regardless.

    Shared libraries are problematic for the analysis: a `.so` is built and
    linked separately, so its bitcode never lands in the target and get-bc cannot
    reach it (only static archives are expanded later). So the auto-detected
    command forces a static build wherever the build system supports it:
    `--disable-shared`/`--enable-static` for an existing `configure` (each added
    only if `configure --help` lists it), `-DBUILD_SHARED_LIBS=OFF` for CMake,
    and `--default-library=static` for Meson -- both unconditional, being
    built-in toggles those tools always accept. The autotools-bootstrap paths add
    `--disable-shared --enable-static` directly, since the configure they
    regenerate does not exist yet to probe and autoconf only warns (never errors)
    on options it does not recognize. Plain make/ninja trees have no portable
    static toggle and are left untouched. An explicit `--build-cmd` bypasses all
    of this.

    LTO is likewise disabled wherever the build system allows it, because gllvm
    cannot extract bitcode from an LTO build (the linker performs codegen itself
    instead of emitting per-object bitcode): `-DCMAKE_INTERPROCEDURAL_OPTIMIZATION=OFF`
    for CMake and `-Db_lto=false` for Meson, both unconditional built-in toggles;
    `--disable-lto` for `configure`-based and autotools-bootstrap builds, probed
    the same way as the static flags.
    """
    def has(*names):
        return any(os.path.exists(os.path.join(project_dir, n)) for n in names)

    if has("configure"):
        flags = "".join(" " + f for f in _configure_flags(project_dir))
        return f"./configure{flags} && make"
    if has("Makefile", "makefile", "GNUmakefile"):
        return "make"
    if has("CMakeLists.txt"):
        return ("cmake -S . -B build -DBUILD_SHARED_LIBS=OFF "
                "-DCMAKE_INTERPROCEDURAL_OPTIMIZATION=OFF && cmake --build build")
    if has("build.ninja"):
        return "ninja"
    if has("meson.build"):
        return ("meson setup build --default-library=static -Db_lto=false "
                "&& ninja -C build")
    if has("autogen.sh"):
        return ("./autogen.sh && ./configure --disable-shared --enable-static "
                "--disable-lto && make")
    if has("configure.ac", "configure.in"):
        return ("autoreconf -i && ./configure --disable-shared --enable-static "
                "--disable-lto && make")
    return None


def _has_bitcode_marker(path):
    """True if `path` embeds gllvm's bitcode-section name (so get-bc can read it)."""
    try:
        with open(path, "rb") as fh:
            mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
            try:
                return any(mm.find(m) != -1 for m in _BC_MARKERS)
            finally:
                mm.close()
    except (OSError, ValueError):
        return False


def _kind_by_ext(path):
    n = os.path.basename(path)
    if n.endswith(".dylib"):
        return "shared"
    if n.endswith(".o"):
        return "object"
    return "exec"


def _classify(path):
    """Return the artifact kind (exec/shared/archive/object) for a built file, or
    None if it is not something get-bc could read."""
    try:
        with open(path, "rb") as fh:
            hdr = fh.read(20)
    except OSError:
        return None
    if len(hdr) < 8:
        return None
    if hdr[:8] == b"!<arch>\n":
        return "archive"
    if hdr[:4] == b"\x7fELF":
        endian = "<H" if hdr[5] != 2 else ">H"
        etype = struct.unpack_from(endian, hdr, 16)[0]
        if etype == 1:
            return "object"
        if etype == 2:
            return "exec"
        if etype == 3:
            name = os.path.basename(path)
            if name.endswith(".so") or ".so." in name:
                return "shared"
            return "exec" if os.access(path, os.X_OK) else "shared"
        return None
    if struct.unpack_from("<I", hdr, 0)[0] in _MACHO_MAGIC:
        return _kind_by_ext(path)
    return None


def find_artifacts(project_dir, newer_than=None):
    """Walk `project_dir` for built files get-bc can read, ranked best-first:
    files carrying gllvm's bitcode section come first, then ones built by this
    run, then by kind (executable > shared lib > archive > object), then newest.
    """
    cands = []
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for f in files:
            p = os.path.join(root, f)
            if os.path.islink(p) or not os.path.isfile(p):
                continue
            kind = _classify(p)
            if kind is None:
                continue
            try:
                mtime = os.path.getmtime(p)
            except OSError:
                continue
            fresh = newer_than is not None and mtime >= newer_than - 2
            cands.append((_has_bitcode_marker(p), fresh, _KIND_RANK[kind], mtime, p))
    cands.sort(reverse=True)
    return [c[-1] for c in cands]


def _artifact_score(path, newer_than=None):
    kind = _classify(path)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0
    fresh = newer_than is not None and mtime >= newer_than - 2
    return (_has_bitcode_marker(path), fresh, _KIND_RANK.get(kind, -1))


def _extract_bc(art, out, archive=False, manifest=False, env=None):
    """Run get-bc on `art` into `out`. `archive` uses -b to build one whole-archive
    module (instead of a lazy bitcode archive); `manifest` also writes the linked
    object list to `<out>.llvm.manifest`. Returns (ok, stderr)."""
    cmd = ["get-bc"]
    if archive:
        cmd.append("-b")
    if manifest:
        cmd.append("-m")
    cmd += ["-o", out, art]
    try:
        r = subprocess.run(cmd, capture_output=True, env=env)
    except OSError as exc:
        return False, f"cannot run get-bc: {exc}"
    try:
        exists = os.path.exists(out) and os.path.getsize(out) > 0
    except OSError as exc:
        return False, f"cannot inspect get-bc output {out}: {exc}"
    ok = r.returncode == 0 and exists
    detail = tail(r.stderr or r.stdout)
    if not ok and not detail:
        detail = f"get-bc exited {r.returncode} without usable output"
    return ok, detail


def _manifest_objects(manifest_path):
    """Existing per-object .bc paths recorded in a get-bc manifest (the objects
    the linker pulled into the artifact)."""
    try:
        with open(manifest_path) as fh:
            lines = [ln.strip() for ln in fh]
    except OSError:
        return []
    return [ln for ln in lines if ln and os.path.exists(ln)]


def _member_name(bc_path):
    """Map a gllvm per-object bitcode path back to its archive member name:
    gllvm names it '.<obj>.bc' next to the object, so '.../.tif_aux.o.bc' maps to
    'tif_aux.o' -- the name `ar t` reports for that member."""
    base = os.path.basename(bc_path)
    if base.endswith(".bc"):
        base = base[:-3]
    if base.startswith("."):
        base = base[1:]
    return base


def _archive_members(archive_path):
    """Member object names (basename) in a static archive, via `ar t`."""
    for tool in ("ar", "llvm-ar"):
        if not shutil.which(tool):
            continue
        try:
            r = subprocess.run([tool, "t", archive_path], capture_output=True)
        except OSError:
            continue
        if r.returncode == 0:
            return {
                os.path.basename(line)
                for line in decode(r.stdout).splitlines()
                if line
            }
    raise AcquireError(f"cannot list static archive members: {archive_path}")


def _bitcode_archives(project_dir):
    """Static archives under `project_dir` that carry gllvm bitcode."""
    found = []
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for f in files:
            p = os.path.join(root, f)
            if os.path.islink(p) or not os.path.isfile(p):
                continue
            if _classify(p) == "archive" and _has_bitcode_marker(p):
                found.append(p)
    return found


def _plan_static_libs(manifest, archive_members, mode):
    """Decide which static archives to fully include.

    manifest: per-object .bc paths the linker pulled into the target.
    archive_members: {archive_path: {member object name, ...}}.
    mode: "auto" includes only archives the target links (members intersect the
    manifest); "all" includes every archive given.

    Returns the chosen archive paths. The caller isolates the target's own
    (non-archive) objects itself, so merging them with the full archives produces
    no duplicate symbols.
    """
    manifest_member_names = {_member_name(p) for p in manifest}
    chosen = []
    ordered = sorted(archive_members.items(),
                     key=lambda kv: len(kv[1]), reverse=True)
    for arch, members in ordered:
        if not members:
            continue
        if mode == "all" or (mode == "auto" and members & manifest_member_names):
            chosen.append(arch)
    return chosen


def _include_static_libs(project_dir, art, kind, primary_bc, mode,
                         work_dir=None, env=None):
    """Replace `primary_bc` with the target's own objects plus the full contents
    of the static archives it links. Returns the replacement bc list, or None when
    there is no relevant archive."""
    archives = [a for a in _bitcode_archives(project_dir)
                if os.path.realpath(a) != os.path.realpath(art)]
    if not archives:
        return None

    manifest = []
    if kind in ("exec", "shared"):
        manifest = _manifest_objects(primary_bc + ".llvm.manifest")
        if not manifest:
            raise AcquireError(
                f"empty target manifest for {os.path.relpath(art, project_dir)}; "
                "cannot complete static-library expansion"
            )

    members = {a: _archive_members(a) for a in archives}
    for archive, names in sorted(members.items()):
        if not names:
            print(f"static library (genuinely empty): {os.path.relpath(archive, project_dir)}")
    chosen = _plan_static_libs(manifest, members, mode)
    if not chosen:
        return None

    extracted = []
    failures = []
    for a in chosen:
        if work_dir:
            tag = hashlib.sha256(os.path.realpath(a).encode()).hexdigest()[:16]
            out = os.path.join(work_dir, f"{os.path.basename(a)}-{tag}.full.bc")
        else:
            out = a + ".full.bc"
        ok, err = _extract_bc(a, out, archive=True, manifest=True, env=env)
        if ok:
            objects = _manifest_objects(out + ".llvm.manifest")
            if objects:
                extracted.append((a, out, objects))
            else:
                failures.append(
                    f"empty full-archive manifest: {os.path.relpath(a, project_dir)}"
                )
        else:
            failures.append(
                f"static-archive extraction failed: "
                f"{os.path.relpath(a, project_dir)}: {err}"
            )
    if failures:
        successful = ", ".join(os.path.relpath(a, project_dir) for a, _, _ in extracted)
        detail = "\n  ".join(failures)
        suffix = f"\n  successful full extractions retained: {successful}" if successful else ""
        raise AcquireError(
            "static-library expansion is incomplete; refusing to publish a "
            f"complete-looking report:\n  {detail}{suffix}"
        )
    if not extracted:
        return None

    if kind in ("exec", "shared"):
        primary_objects = {os.path.realpath(p) for p in manifest}
        if mode == "auto":
            extracted = [
                item for item in extracted
                if primary_objects & {os.path.realpath(p) for p in item[2]}
            ]
            if not extracted:
                return None
        ordered = sorted(extracted, key=lambda item: len(item[2]), reverse=True)
        extracted = []
        covered_objects = set()
        for item in ordered:
            objects = {os.path.realpath(p) for p in item[2]}
            if objects <= covered_objects:
                continue
            extracted.append(item)
            covered_objects |= objects
        covered = {
            os.path.realpath(p)
            for _, _, objects in extracted
            for p in objects
        }
        parts = [p for p in manifest if os.path.realpath(p) not in covered]
    else:
        parts = [primary_bc]
    lib_bcs = []
    for a, out, _ in extracted:
        lib_bcs.append(out)
        print(f"static library (full): {os.path.relpath(a, project_dir)}")
    return parts + lib_bcs


def _run_build(cmd, project_dir, env, verbose):
    """Run the build, returning (returncode, combined_output). In verbose mode the
    output is streamed live and also captured, so cache detection and diagnostics
    still see it."""
    try:
        if not verbose:
            with tempfile.TemporaryFile() as log:
                proc = subprocess.Popen(
                    cmd, cwd=project_dir, env=env, stdout=log,
                    stderr=subprocess.STDOUT,
                )
                proc.wait()
                size = log.tell()
                log.seek(max(0, size - 1024 * 1024))
                return proc.returncode, decode(log.read())
        proc = subprocess.Popen(
            cmd, cwd=project_dir, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        chunks = deque()
        length = 0
        for raw in proc.stdout:
            line = decode(raw)
            sys.stdout.write(line)
            chunks.append(line)
            length += len(line)
            while length > 1024 * 1024 and chunks:
                length -= len(chunks.popleft())
        proc.wait()
        return proc.returncode, "".join(chunks)
    except OSError as exc:
        raise AcquireError(f"cannot run build command {cmd[0]}: {exc}") from exc


def acquire_c_bitcode(project_dir, tc, artifact=None, build_cmd=None,
                      static_libs="auto", verbose=False, optimize=False,
                      work_dir=None):
    """Build `project_dir` with gllvm wrappers and extract its bitcode.

    artifact: path (relative to project_dir) of the built binary/object/archive.
    When None, the build product is auto-detected. An explicit path is strict.
    static_libs: "auto" (default) also extracts, in full, every static archive
    the target links; "none" keeps only the linker's view; "all" pulls in every
    bitcode archive in the tree (see the module docstring).
    verbose: stream the build's output live instead of capturing it silently.
    optimize: when False (default) emit source-faithful bitcode (functions not
    inlined away, via LLVM_BITCODE_GENERATION_FLAGS); when True leave inlining to
    the build's flags.

    Returns a list of absolute .bc paths to be linked together.
    """
    if not shutil.which("gclang"):
        raise AcquireError("gclang not found on PATH; run scripts/setup.sh")
    if work_dir is None:
        work_dir = tempfile.mkdtemp(prefix="reach-c-")
        atexit.register(shutil.rmtree, work_dir, ignore_errors=True)
    clang_bindir = os.path.dirname(os.path.abspath(tc.clang))
    env = _gllvm_env(
        _build_env(clang_bindir, optimize=optimize), tc,
        tool_dir=os.path.join(work_dir, "gllvm-tools"),
    )
    cmd = build_cmd or ["make"]
    before = time.time()
    if verbose:
        sys.stdout.flush()
    rc, build_output = _run_build(cmd, project_dir, env, verbose)
    if rc != 0:
        detail = "" if verbose else f":\n{build_output}"
        raise AcquireError(f"build failed (exit {rc}){detail}")

    cached = _build_looks_cached(build_output)

    errors = []

    def _no_bitcode(base):
        diag = diagnostics.diagnose_build(build_output, errors)
        if diag:
            cause, remedy = diag
            return AcquireError(f"{base}\n\nLikely cause: {cause}\n{remedy}")
        return AcquireError(base)

    explicit = os.path.abspath(os.path.join(project_dir, artifact)) if artifact else None
    if explicit:
        if not os.path.exists(explicit):
            raise AcquireError(f"explicit artifact does not exist: {explicit}")
        explicit_kind = _classify(explicit)
        if explicit_kind is None:
            raise AcquireError(f"explicit artifact is unsupported: {explicit}")
        print(f"artifact: {explicit}")
        candidates = [explicit]
    else:
        cutoff = None if cached else before
        candidates = find_artifacts(project_dir, newer_than=cutoff)
        if len(candidates) > 1:
            best = _artifact_score(candidates[0], cutoff)
            tied = [p for p in candidates if _artifact_score(p, cutoff) == best]
            if len(tied) > 1:
                ranked = "\n  ".join(os.path.relpath(p, project_dir) for p in tied)
                raise AcquireError(
                    "multiple equally plausible build artifacts remain; pass "
                    f"--artifact with one of:\n  {ranked}"
                )
    if not candidates:
        raise _no_bitcode(
            f"no build artifact with embedded bitcode found under {project_dir}; "
            "pass --artifact PATH to the built binary/object/archive")

    art = kind = primary = None
    for cand in candidates:
        ck = _classify(cand)
        tag = hashlib.sha256(os.path.realpath(cand).encode()).hexdigest()[:16]
        out = os.path.join(work_dir, f"{os.path.basename(cand)}-{tag}.bc")
        ok, err = _extract_bc(cand, out, archive=(ck == "archive"),
                              manifest=(ck in ("exec", "shared")), env=env)
        if ok:
            art, kind, primary = cand, ck, out
            if not explicit:
                print(f"artifact: {os.path.relpath(cand, project_dir)}")
            break
        errors.append(f"{os.path.relpath(cand, project_dir)}: {err}")
    if primary is None:
        if explicit:
            raise _no_bitcode(
                f"get-bc could not extract bitcode from explicit artifact {explicit}:\n  "
                + "\n  ".join(errors)
            )
        raise _no_bitcode(
            "get-bc could not extract bitcode from any detected artifact:\n  "
            + "\n  ".join(errors))

    if build_is_cached(build_output, art, before):
        print("warning: the build is CACHED (nothing was recompiled); the "
              "extracted bitcode reflects the existing artifact, not this run. "
              "Rebuild from clean if the target or its flags changed.")

    if static_libs != "none":
        expanded = _include_static_libs(
            project_dir, art, kind, primary, static_libs,
            work_dir=work_dir, env=env,
        )
        if expanded is not None:
            return expanded
    return [primary]
