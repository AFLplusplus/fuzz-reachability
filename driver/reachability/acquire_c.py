"""C / C++ bitcode acquisition via gllvm.

Build the project with the gclang/gclang++ wrappers (which embed bitcode-path
metadata into each object), then run get-bc on the built artifact to extract a
whole-program .bc. The artifact is auto-detected from the build output when not
given explicitly. Independent of the project's own LTO setup.

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
provide exact object provenance; incomplete extraction falls back atomically to the
linker's view.
"""

import mmap
import os
import shutil
import struct
import subprocess
import sys
import time


class AcquireError(RuntimeError):
    pass


_SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv",
              ".cache"}
_BC_MARKERS = (b".llvm_bc", b"__llvm_bc")
_KIND_RANK = {"exec": 3, "shared": 2, "archive": 1, "object": 0}
_MACHO_MAGIC = (0xFEEDFACE, 0xFEEDFACF, 0xCEFAEDFE, 0xCFFAEDFE, 0xCAFEBABE)


def _build_looks_cached(output):
    """True when the build tool reported it (re)compiled nothing, so the
    artifact bitcode reflects an earlier compile rather than this run."""
    t = output or ""
    return any(m in t for m in ("Nothing to be done", " is up to date",
                                "ninja: no work to do", "Nothing to do"))


def _build_env(clang_bindir: str) -> dict:
    env = dict(os.environ)
    env["CC"] = "gclang"
    env["CXX"] = "gclang++"
    env["LLVM_COMPILER_PATH"] = clang_bindir
    return env


def detect_build_cmd(project_dir):
    """Pick a build command for a C/C++ project by probing for the well-known
    build files, in the order configure -> make -> cmake -> ninja -> meson, with
    an autotools-bootstrap fallback. Returns a shell command string, or None if
    nothing is recognized (the caller then falls back to plain `make`). The
    gllvm wrappers are injected via CC/CXX, which every build system below
    honours at configure time, so the chosen command embeds bitcode regardless.
    """
    def has(*names):
        return any(os.path.exists(os.path.join(project_dir, n)) for n in names)

    if has("configure"):
        return "./configure && make"
    if has("Makefile", "makefile", "GNUmakefile"):
        return "make"
    if has("CMakeLists.txt"):
        return "cmake -S . -B build && cmake --build build"
    if has("build.ninja"):
        return "ninja"
    if has("meson.build"):
        return "meson setup build && ninja -C build"
    if has("autogen.sh"):
        return "./autogen.sh && ./configure && make"
    if has("configure.ac", "configure.in"):
        return "autoreconf -i && ./configure && make"
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


def _extract_bc(art, out, archive=False, manifest=False):
    """Run get-bc on `art` into `out`. `archive` uses -b to build one whole-archive
    module (instead of a lazy bitcode archive); `manifest` also writes the linked
    object list to `<out>.llvm.manifest`. Returns (ok, stderr)."""
    cmd = ["get-bc"]
    if archive:
        cmd.append("-b")
    if manifest:
        cmd.append("-m")
    cmd += ["-o", out, art]
    r = subprocess.run(cmd, capture_output=True, text=True)
    ok = r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0
    return ok, r.stderr.strip()


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
    """Member object names (basename) in a static archive, via `ar t`. Empty set
    if the archive cannot be listed."""
    for tool in ("ar", "llvm-ar"):
        if not shutil.which(tool):
            continue
        r = subprocess.run([tool, "t", archive_path], capture_output=True, text=True)
        if r.returncode == 0:
            return {os.path.basename(m) for m in r.stdout.split() if m}
    return set()


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
    """Decide which archives to fully include and which manifest objects are the
    target's own (non-archive) roots.

    manifest: per-object .bc paths the linker pulled into the target.
    archive_members: {archive_path: {member object name, ...}}.
    mode: "auto" includes only archives the target links (members intersect the
    manifest); "all" includes every archive given.

    Returns (chosen_archive_paths, root_bc_paths): the roots are the manifest
    objects that belong to no chosen archive, so merging them with the full
    archives produces no duplicate symbols.
    """
    manifest_member_names = {_member_name(p) for p in manifest}
    chosen = []
    union = set()
    ordered = sorted(archive_members.items(),
                     key=lambda kv: len(kv[1]), reverse=True)
    for arch, members in ordered:
        if not members:
            continue
        if mode == "all" or (mode == "auto" and members & manifest_member_names):
            chosen.append(arch)
            union |= members
    roots = [p for p in manifest if _member_name(p) not in union]
    return chosen, roots


def _include_static_libs(project_dir, art, kind, primary_bc, mode):
    """Replace `primary_bc` with the target's own objects plus the full contents
    of the static archives it links. Returns the replacement bc list, or None to
    keep just `primary_bc` (no relevant archive, or the target's objects could not
    be isolated from it)."""
    archives = [a for a in _bitcode_archives(project_dir)
                if os.path.realpath(a) != os.path.realpath(art)]
    if not archives:
        return None

    manifest = []
    if kind in ("exec", "shared"):
        manifest = _manifest_objects(primary_bc + ".llvm.manifest")

    members = {a: _archive_members(a) for a in archives}
    chosen, _ = _plan_static_libs(manifest, members, mode)
    if not chosen:
        return None

    extracted = []
    failed = False
    for a in chosen:
        out = a + ".full.bc"
        ok, err = _extract_bc(a, out, archive=True, manifest=True)
        if ok:
            objects = _manifest_objects(out + ".llvm.manifest")
            if objects:
                extracted.append((a, out, objects))
            else:
                failed = True
                print(f"warning: full bitcode manifest is empty for "
                      f"{os.path.relpath(a, project_dir)}")
        else:
            failed = True
            print(f"warning: could not extract full bitcode from "
                  f"{os.path.relpath(a, project_dir)}: {err}")
    if failed or not extracted:
        if extracted:
            print("warning: static-library expansion was incomplete; keeping the "
                  "linker's view only")
        return None

    if kind in ("exec", "shared"):
        if not manifest:
            print("warning: could not read the target object manifest; keeping the "
                  "linker's view only")
            return None
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


def acquire_c_bitcode(project_dir, tc, artifact=None, build_cmd=None,
                      static_libs="auto", verbose=False):
    """Build `project_dir` with gllvm wrappers and extract its bitcode.

    artifact: path (relative to project_dir) of the built binary/object/archive.
    When None (or it does not exist after the build), the build product is
    auto-detected.
    static_libs: "auto" (default) also extracts, in full, every static archive
    the target links; "none" keeps only the linker's view; "all" pulls in every
    bitcode archive in the tree (see the module docstring).
    verbose: stream the build's output live instead of capturing it silently.

    Returns a list of absolute .bc paths to be linked together.
    """
    if not shutil.which("gclang"):
        raise AcquireError("gclang not found on PATH; run scripts/setup.sh")
    clang_bindir = os.path.dirname(os.path.abspath(tc.clang))
    env = _build_env(clang_bindir)
    cmd = build_cmd or ["make"]
    before = time.time()
    if verbose:
        sys.stdout.flush()
    r = subprocess.run(cmd, cwd=project_dir, env=env,
                       capture_output=not verbose, text=True)
    if r.returncode != 0:
        detail = "" if verbose else f":\n{r.stdout}\n{r.stderr}"
        raise AcquireError(f"build failed (exit {r.returncode}){detail}")

    cached = not verbose and _build_looks_cached((r.stdout or "") + "\n" + (r.stderr or ""))
    if cached:
        print("warning: the build is CACHED (nothing was recompiled); the "
              "extracted bitcode reflects the existing artifact, not this run. "
              "Rebuild from clean if the target or its flags changed.")

    explicit = os.path.join(project_dir, artifact) if artifact else None
    if explicit and os.path.exists(explicit):
        candidates = [explicit]
    else:
        if explicit:
            print(f"warning: --artifact {artifact!r} not found after build; "
                  "auto-detecting the build product")
        candidates = find_artifacts(project_dir, newer_than=None if cached else before)
    if not candidates:
        raise AcquireError(
            f"no build artifact with embedded bitcode found under {project_dir}; "
            "pass --artifact PATH to the built binary/object/archive")

    errors = []
    art = kind = primary = None
    for cand in candidates[:8]:
        ck = _classify(cand)
        out = cand + ".bc"
        ok, err = _extract_bc(cand, out, archive=(ck == "archive"),
                              manifest=(ck in ("exec", "shared")))
        if ok:
            art, kind, primary = cand, ck, out
            if not (explicit and cand == explicit):
                print(f"artifact: {os.path.relpath(cand, project_dir)}")
            break
        errors.append(f"{os.path.relpath(cand, project_dir)}: {err}")
    if primary is None:
        raise AcquireError(
            "get-bc could not extract bitcode from any detected artifact:\n  "
            + "\n  ".join(errors))

    if static_libs != "none":
        expanded = _include_static_libs(project_dir, art, kind, primary, static_libs)
        if expanded is not None:
            return expanded
    return [primary]
