"""Command-line front-end: chains the pipeline stages end-to-end.

  reachability check-toolchain
  reachability run --project DIR --lang {c,cpp,rust,mixed,libfuzzer,ziggy,afl} [--out FILE] [...]
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

from . import (__version__, acquire_c, acquire_rust, analyze, link, outputs,
               report, toolchain)

# --lang selects a target type: a source language (how to acquire bitcode) or a
# fuzz-harness shape (which also implies the default entry point). Each maps to
# (acquire mode, default entries). Entries are resolved flexibly by the analyzer
# (mangled, demangled, '::name' suffix, or the 'fuzz_target!' alias), so harness
# targets never need a mangled symbol. C/C++ default to both `main` and
# `LLVMFuzzerTestOneInput`, covering a normal program and a libFuzzer harness;
# plain Rust defaults to `main`. A default that matches nothing is a harmless
# warning (roots are unioned), so a target with only one of them still analyzes.
# libfuzzer/ziggy/afl are the Rust harness shapes.
TARGETS = {
    "c":         ("c",     ["main", "LLVMFuzzerTestOneInput"]),
    "cpp":       ("cpp",   ["main", "LLVMFuzzerTestOneInput"]),
    "rust":      ("rust",  ["main"]),
    "mixed":     ("mixed", ["LLVMFuzzerTestOneInput"]),
    "libfuzzer": ("rust",  ["fuzz_target!"]),
    "ziggy":     ("rust",  ["main"]),
    "afl":       ("rust",  ["main"]),
}

_RUST_NATIVE = {
    "afl":       ["cargo", "afl", "build"],
    "ziggy":     ["cargo", "ziggy", "build", "--no-honggfuzz"],
    "libfuzzer": ["cargo", "fuzz", "build"],
}


def _native_build_cmd(lang, profile):
    cmd = list(_RUST_NATIVE[lang])
    if profile == "release":
        cmd.append("--release")
    return cmd


def default_analyzer():
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    path = os.environ.get("REACHABILITY_ANALYZER") or os.path.join(
        repo, "analyzer", "build", "reachability-analyzer")
    hint = "build it with `make build`, or set $REACHABILITY_ANALYZER"
    if not os.path.isfile(path):
        raise toolchain.ToolchainError(f"analyzer binary not found: {path}\n{hint}")
    return path


def _acquire(args, tc, verbose=False, work_dir=None):
    """Return the list of .bc files for the project per --lang."""
    mode = TARGETS[args.lang][0]
    bcs = []
    if mode in ("c", "cpp", "mixed"):
        # An explicit --build-cmd wins; otherwise auto-detect the build system
        # from the project's files. Either runs under a shell so it can be a
        # compound command; gllvm wrappers are injected via env.
        build = args.build_cmd or acquire_c.detect_build_cmd(args.project)
        if args.build_cmd:
            if verbose:
                print(f"build command: {build} (from --build-cmd)")
        else:
            print(f"build command: {build or 'make'}"
                  f"{'' if build else ' (default; no build system detected)'}")
        build_cmd = ["sh", "-c", build] if build else None
        before = _snapshot_c_artifacts(args.project)
        try:
            bcs.extend(
                acquire_c.acquire_c_bitcode(
                    args.project, tc, args.artifact, build_cmd,
                    static_libs=args.static_libs, verbose=verbose,
                    optimize=args.optimize, work_dir=work_dir,
                )
            )
        finally:
            _record_c_artifacts(args.project, before)
    if mode in ("rust", "mixed"):
        if args.lang in _RUST_NATIVE:
            cmd = args.build_cmd or _native_build_cmd(args.lang, args.profile)
            shell = args.build_cmd is not None
            if verbose and not shell:
                print(f"build command: {' '.join(cmd)} (native {args.lang} build)")
            elif args.build_cmd:
                print(f"build command: {args.build_cmd} (from --build-cmd)")
            bcs.extend(
                acquire_rust.acquire_rust_bitcode_native(
                    args.project, cmd, shell=shell, verbose=verbose,
                    optimize=args.optimize, mangling=args.mangling,
                    work_dir=work_dir,
                )
            )
            if not args.optimize:
                _native_clean(args.project, args.lang, verbose)
        else:
            bcs.extend(
                acquire_rust.acquire_rust_bitcode(
                    args.project, profile=(args.profile or "debug"),
                    build_std=args.build_std, codegen_units=args.codegen_units,
                    verbose=verbose, optimize=args.optimize,
                    mangling=args.mangling,
                )
            )
    return bcs


_NATIVE_CLEAN = {
    "afl": (["cargo", "afl", "clean"], "."),
    "ziggy": (["cargo", "ziggy", "clean"], "."),
    "libfuzzer": (["cargo", "clean"], "fuzz"),
}


def _native_clean(project_dir, lang, verbose):
    """Remove the throwaway opt-0 instrumented build a native analysis run
    produced, using the harness's own clean (cargo-fuzz has none, so its fuzz/
    target is cleaned with plain cargo clean). Best-effort: a missing tool or a
    nonzero exit is ignored -- the build is analysis scratch, not the user's."""
    spec = _NATIVE_CLEAN.get(lang)
    if not spec:
        return
    argv, subdir = spec
    cwd = project_dir if subdir == "." else os.path.join(project_dir, subdir)
    if not os.path.isdir(cwd) or not shutil.which(argv[0]):
        return
    if verbose:
        print(f"  cleaning native build: {' '.join(argv)} ({cwd})")
    try:
        subprocess.run(argv, cwd=cwd, capture_output=not verbose, text=True)
    except OSError:
        return


_C_CLEAN_SUFFIXES = (".bc", ".o", ".llvm.manifest")
_OWNED_DIR = ".reachability-cache"
_OWNED_FILE = "owned-c-artifacts.json"


def _snapshot_c_artifacts(project):
    state = {}
    for root, dirs, files in os.walk(project):
        dirs[:] = [d for d in dirs if d not in acquire_c._SKIP_DIRS]
        for name in files:
            if not name.endswith(_C_CLEAN_SUFFIXES):
                continue
            path = os.path.join(root, name)
            try:
                stat = os.stat(path)
            except OSError:
                continue
            state[os.path.abspath(path)] = (stat.st_mtime_ns, stat.st_size)
    return state


def _owned_manifest(project):
    return os.path.join(project, _OWNED_DIR, _OWNED_FILE)


def _load_owned_artifacts(project):
    try:
        with open(_owned_manifest(project), encoding="utf-8") as fh:
            values = json.load(fh)
    except (OSError, json.JSONDecodeError, UnicodeError):
        return []
    return [value for value in values if isinstance(value, str)]


def _record_c_artifacts(project, before):
    after = _snapshot_c_artifacts(project)
    owned = set(_load_owned_artifacts(project))
    root = os.path.realpath(project)
    for path, state in after.items():
        if before.get(path) == state:
            continue
        real = os.path.realpath(path)
        try:
            if os.path.commonpath([root, real]) != root:
                continue
        except ValueError:
            continue
        owned.add(os.path.relpath(path, project))
    if not owned:
        return
    directory = os.path.join(project, _OWNED_DIR)
    try:
        os.makedirs(directory, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".owned-", dir=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(sorted(owned), fh)
            fh.write("\n")
        os.replace(temporary, _owned_manifest(project))
    except OSError as exc:
        print(f"warning: could not record analysis-owned C artifacts: {exc}",
              file=sys.stderr)


def _cargo_clean(directory, verbose=False):
    """Drop a Cargo target dir so the next build recompiles every crate and
    re-emits bitcode: run `cargo clean` when a manifest is present, else (or on
    failure) remove `target/` directly."""
    target = acquire_rust._target_dir(directory)
    ran = False
    if shutil.which("cargo") and os.path.exists(os.path.join(directory, "Cargo.toml")):
        if verbose:
            print(f"  cargo clean ({directory})")
        try:
            r = subprocess.run(["cargo", "clean"], cwd=directory,
                               capture_output=not verbose, text=True)
            ran = r.returncode == 0
        except OSError:
            ran = False
    if not ran and os.path.isdir(target):
        shutil.rmtree(target, ignore_errors=True)
        if verbose:
            print(f"  removed {target}")


def _build_clean_cmd(directory):
    """The in-place clean invocation for a configured build tree at `directory`,
    chosen from the build-system files it holds, or None when none is present.
    `ninja -t clean` covers meson and cmake's ninja generator as well as a plain
    ninja tree; `make clean` covers in-source make and cmake's make generator;
    `cmake --build --target clean` is the generator-agnostic fallback."""
    def has(*names):
        return any(os.path.exists(os.path.join(directory, n)) for n in names)
    if has("build.ninja"):
        return ["ninja", "-C", directory, "-t", "clean"]
    if has("Makefile", "makefile", "GNUmakefile"):
        return ["make", "-C", directory, "clean"]
    if has("CMakeCache.txt"):
        return ["cmake", "--build", directory, "--target", "clean"]
    return None


def _run_clean(cmd, directory, verbose):
    """Run a build-system clean in `directory`, skipping it when the tool is not
    installed. Output is captured unless verbose; a non-zero exit (e.g. a tree
    with no clean target) is ignored — the object-file sweep is the backstop."""
    if not shutil.which(cmd[0]):
        if verbose:
            print(f"  skip clean in {directory}: {cmd[0]} not installed")
        return
    if verbose:
        print(f"  {' '.join(cmd)}")
    try:
        subprocess.run(cmd, cwd=directory, capture_output=not verbose, text=True)
    except OSError:
        return


def _clean_c_artifacts(project, drop, verbose=False):
    """Clean a C/C++ build in place under `project` without deleting build
    directories — some projects build in-source and have none. Each configured
    build tree is cleaned with its own tool (make/ninja/cmake/meson, see
    `_build_clean_cmd`), then files recorded as analysis-owned are removed."""
    cleaned = []
    for root, dirs, files in os.walk(project):
        dirs[:] = [d for d in dirs if d not in acquire_c._SKIP_DIRS]
        if not any(root == c or root.startswith(c + os.sep) for c in cleaned):
            cmd = _build_clean_cmd(root)
            if cmd is not None:
                _run_clean(cmd, root, verbose)
                cleaned.append(root)
    project_real = os.path.realpath(project)
    for relative in _load_owned_artifacts(project):
        path = os.path.abspath(os.path.join(project, relative))
        try:
            if os.path.commonpath([project_real, os.path.realpath(path)]) != project_real:
                continue
        except ValueError:
            continue
        drop(path)
    cache = os.path.join(project, _OWNED_DIR)
    if os.path.isdir(cache):
        shutil.rmtree(cache)
        if verbose:
            print(f"  removed {cache}")


def _clean_project(args, verbose=False, output_paths=None):
    """Remove selected outputs and tool-owned build state after validating every
    destination. Rust targets use cargo clean. C/C++ targets use configured clean
    commands and remove only paths recorded in the ownership manifest."""
    output_paths = output_paths or outputs.resolve(args)
    project = args.project
    removed = 0

    def drop(path):
        nonlocal removed
        try:
            if os.path.islink(path) or os.path.isfile(path):
                os.remove(path)
            else:
                return
        except OSError as e:
            print(f"warning: could not remove {path}: {e}", file=sys.stderr)
            return
        removed += 1
        if verbose:
            print(f"  removed {path}")

    drop(os.path.join(project, "merged.bc"))
    for _, path in output_paths.items():
        drop(path)

    mode = TARGETS[args.lang][0]
    if mode in ("rust", "mixed"):
        _cargo_clean(project, verbose)
        fuzz = os.path.join(project, "fuzz")
        if os.path.isdir(fuzz):
            _cargo_clean(fuzz, verbose)
    if mode in ("c", "cpp", "mixed"):
        _clean_c_artifacts(project, drop, verbose)

    print(f"clean: removed {removed} cached path(s) under {project}")


def cmd_run(args):
    v = args.verbose
    if args.backend is not None:
        print("warning: --backend is deprecated and ignored; the type-based "
              "backend is always used", file=sys.stderr)
    output_paths = outputs.resolve(args)
    rust_target = TARGETS[args.lang][0] in ("rust", "mixed")
    tc = toolchain.check_coherence(default_analyzer(), require_rust=rust_target)
    if v:
        rust_version = f" (rustc LLVM {tc.rustc_major})" if rust_target else ""
        print(f"==> [1/4] toolchain: LLVM {tc.llvm_major}{rust_version}")
        print(f"  clang     {tc.clang}")
        print(f"  clang++   {tc.clangxx}")
        print(f"  llvm-link {tc.llvm_link}")
        print(f"  analyzer  {tc.analyzer}")
    if rust_target:
        toolchain.assert_rust_bitcode_readable(tc)

    if args.clean:
        if v:
            print("==> cleaning cached build artifacts and prior outputs")
        _clean_project(args, verbose=v, output_paths=output_paths)

    if v:
        print(f"==> [2/4] acquiring bitcode (lang={args.lang})")
    try:
        with tempfile.TemporaryDirectory(prefix="reachability-run-") as work_dir:
            with outputs.Transaction(output_paths) as transaction:
                bcs = _acquire(args, tc, verbose=v, work_dir=work_dir)
                if v:
                    print(f"  collected {len(bcs)} bitcode module(s):")
                    for b in bcs:
                        print(f"    {b}")

                merged = os.path.join(work_dir, "merged.bc")
                if v:
                    print(f"==> [3/4] merging {len(bcs)} module(s) with llvm-link -> {merged}")
                link.link_bitcode(bcs, merged, tc)

                if v:
                    print(f"==> [4/4] analyzing from entries [{', '.join(args.entry)}]")
                result = analyze.analyze(
                    merged, tc, args.entry,
                    dot=transaction.path("--dot") if output_paths.dot else None,
                    reached_out=transaction.path("--reached"),
                    not_reached_out=transaction.path("--not-reached"),
                    out_path=transaction.path("--out"), verbose=v,
                    include_process_lifecycle_roots=(
                        getattr(args, "include_process_lifecycle_roots", False)
                    ),
                )
                transaction.publish()
    except OSError as exc:
        raise outputs.OutputError(f"pipeline temporary-file failure: {exc}") from exc
    report.print_summary(result)
    advisory = report.external_advisory(result)
    if advisory:
        print(advisory)
    print(f"wrote {output_paths.json}")
    print(f"wrote {output_paths.reached}  (sancov allowlist of reachable functions)")
    print(f"wrote {output_paths.not_reached}  (sancov ignorelist of unreachable functions)")
    return 0


def cmd_check_toolchain(args):
    tc = toolchain.check_coherence(default_analyzer(), require_rust=True)
    print(f"OK: analyzer toolchain on LLVM {tc.llvm_major} "
          f"(min {toolchain.MIN_LLVM_MAJOR}); rustc LLVM {tc.rustc_major}")
    print(f"  clang     {tc.clang}")
    print(f"  clang++   {tc.clangxx}")
    print(f"  llvm-link {tc.llvm_link}")
    print(f"  analyzer  {tc.analyzer}")
    print(f"  gllvm CC  {tc.clang}")
    print(f"  gllvm CXX {tc.clangxx}")
    print(f"  gllvm link {tc.llvm_link}")
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="reachability",
        description=f"reachability v{__version__} — static fuzz-reachability analyzer",
    )
    p.add_argument("--version", action="version",
                   version=f"reachability v{__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check-toolchain").set_defaults(func=cmd_check_toolchain)

    r = sub.add_parser("run")
    r.add_argument("--project", required=True)
    r.add_argument("--lang", required=True, choices=list(TARGETS),
                   help="target type: source language (c/cpp/rust/mixed) or Rust "
                        "fuzz harness (libfuzzer/ziggy/afl). The harness types set "
                        "the default entry: libfuzzer->fuzz_target!, ziggy/afl->main")
    r.add_argument("--artifact", default=None,
                   help="C/C++: built binary/object/archive to extract bitcode "
                        "from, relative to --project (default: auto-detect the "
                        "build product)")
    r.add_argument("--build-cmd", default=None, dest="build_cmd",
                   help="shell build command. C/C++ (default: auto-detected from "
                        "configure/Makefile/CMakeLists.txt/build.ninja/meson.build, "
                        "else make; e.g. 'cmake -S . -B build && cmake --build "
                        "build'). For libfuzzer/ziggy/afl it overrides the native "
                        "build command (default `cargo fuzz build` / `cargo ziggy "
                        "build --no-honggfuzz` / `cargo afl build`)")
    r.add_argument("--static-libs", default="auto",
                   choices=["auto", "none", "all"],
                   help="C/C++: how to treat static archives (.a) the target "
                        "links. 'auto' (default) also analyzes the full contents "
                        "of each linked archive (so members the linker discarded "
                        "are reported, not silently dropped); 'none' keeps only "
                        "the linker's view; 'all' includes every bitcode archive "
                        "in the tree")
    r.add_argument("--entry", action="append", default=None,
                   help="entry function (repeatable; overrides the --lang default). "
                        "Accepts a mangled symbol, a demangled name, a '::name' "
                        "suffix like 'main', or the alias 'fuzz_target!'")
    r.add_argument("--include-process-lifecycle-roots", action="store_true",
                   dest="include_process_lifecycle_roots",
                   help="also root constructors, destructors, ifunc resolvers, "
                        "and a defined LLVMFuzzerInitialize (default: off)")
    r.add_argument("--backend", default=None,
                   help="deprecated and ignored; the type-based backend is "
                        "always used")
    r.add_argument("--profile", default=None, choices=["debug", "release"],
                   help="build profile. For libfuzzer/ziggy/afl, 'release' adds "
                        "--release to the native command (else the tool's default). "
                        "For plain --lang rust it is the cargo profile (default "
                        "debug); match the fuzz binary's profile so generic sharing "
                        "(opt level) lines up")
    r.add_argument("--codegen-units", type=int, default=None, dest="codegen_units",
                   help="-Ccodegen-units for plain --lang rust builds; match the "
                        "fuzz binary's value so inlining lines up. Default: the "
                        "project's Cargo.toml [profile.<name>] codegen-units, else "
                        "cargo's per-profile default (debug 256, release 16). "
                        "Ignored for libfuzzer/ziggy/afl (their build sets it)")
    r.add_argument("--build-std", action="store_true", dest="build_std")
    r.add_argument("--mangling", default="auto", choices=["auto", "legacy", "v0"],
                   help="Rust symbol-mangling scheme for the analysis build "
                        "(all Rust --lang values). 'auto' (default) uses "
                        "rustc's own default (legacy on stable toolchains); "
                        "'legacy'/'v0' force -Csymbol-mangling-version=<v> so "
                        "the analysis bitcode's Rust symbols match the target "
                        "you join against (e.g. pass 'v0' to match a "
                        "-Cinstrument-coverage coverage build, which is v0)")
    r.add_argument(
        "--optimize", action="store_true", dest="optimize",
        help="build at the target's real optimization (post-inline). By default "
             "the analysis build is source-faithful (C/C++ via -fno-inline; Rust "
             "via -Copt-level=0), so reachability matches llvm-cov and is a safe "
             "allowlist superset. Controls inlining only; LTO is still stripped.")
    r.add_argument("--clean", action="store_true",
                   help="remove prior selected output files and tool-owned build "
                        "state before rebuilding. Rust uses cargo clean; C/C++ "
                        "uses configured build-system clean targets and removes "
                        "only artifacts recorded as produced by this tool")
    r.add_argument("--dot", default=None)
    r.add_argument("--reached", default=None,
                   help="sancov allowlist path (default: reached.txt next to --out)")
    r.add_argument("--not-reached", default=None, dest="not_reached",
                   help="sancov ignorelist path (default: not_reached.txt next to --out)")
    r.add_argument("--out", default=None,
                   help="output JSON file path (default: reachability.json "
                        "in --project)")
    r.add_argument("-v", "--verbose", action="store_true",
                   help="narrate each pipeline stage (toolchain, build, merge, "
                        "analyze): echo the tool commands, stream the build "
                        "output, and list the collected bitcode.")
    r.set_defaults(func=cmd_run)
    return p


def main(argv=None):
    parser = build_parser()
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        parser.print_help()
        return 0
    args = parser.parse_args(argv)
    if getattr(args, "entry", None) is None and args.cmd == "run":
        args.entry = list(TARGETS[args.lang][1])
    try:
        return args.func(args)
    except (toolchain.ToolchainError, acquire_c.AcquireError,
            acquire_rust.AcquireError, link.LinkError, analyze.AnalyzeError,
            outputs.OutputError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
