"""Command-line front-end: chains the pipeline stages end-to-end.

  reachability check-toolchain
  reachability run --project DIR --lang {c,cpp,rust,mixed,libfuzzer,ziggy,afl} [--out FILE] [...]
"""

import argparse
import json
import os
import sys

from . import acquire_c, acquire_rust, analyze, link, report, toolchain

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


def _acquire(args, tc, verbose=False):
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
        bcs.extend(
            acquire_c.acquire_c_bitcode(
                args.project, tc, args.artifact, build_cmd,
                static_libs=args.static_libs, verbose=verbose,
            )
        )
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
                    args.project, cmd, shell=shell, verbose=verbose
                )
            )
        else:
            bcs.extend(
                acquire_rust.acquire_rust_bitcode(
                    args.project, profile=(args.profile or "debug"),
                    build_std=args.build_std, codegen_units=args.codegen_units,
                    verbose=verbose
                )
            )
    return bcs


def cmd_run(args):
    v = args.verbose
    if args.backend is not None:
        print("warning: --backend is deprecated and ignored; the type-based "
              "backend is always used", file=sys.stderr)
    if args.out is None:
        args.out = os.path.join(args.project, "reachability.json")
    elif os.path.isdir(args.out):
        args.out = os.path.join(args.out, "reachability.json")
    rust_target = TARGETS[args.lang][0] in ("rust", "mixed")
    tc = toolchain.check_coherence(default_analyzer(), require_rust=rust_target)
    if v:
        rust_version = f" (rustc LLVM {tc.rustc_major})" if rust_target else ""
        print(f"==> [1/4] toolchain: LLVM {tc.llvm_major}{rust_version}")
        print(f"  clang     {tc.clang}")
        print(f"  clang++   {tc.clangxx}")
        print(f"  llvm-link {tc.llvm_link}")
        print(f"  opt       {tc.opt}")
        print(f"  analyzer  {tc.analyzer}")
    if rust_target:
        toolchain.assert_rust_bitcode_readable(tc)

    if v:
        print(f"==> [2/4] acquiring bitcode (lang={args.lang})")
    bcs = _acquire(args, tc, verbose=v)
    if v:
        print(f"  collected {len(bcs)} bitcode module(s):")
        for b in bcs:
            print(f"    {b}")

    merged = os.path.join(args.project, "merged.bc")
    if v:
        print(f"==> [3/4] merging {len(bcs)} module(s) with llvm-link -> {merged}")
    link.link_bitcode(bcs, merged, tc)

    # The two sancov lists land next to the JSON output (override with flags).
    outdir = os.path.dirname(os.path.abspath(args.out))
    reached = args.reached or os.path.join(outdir, "reached.txt")
    not_reached = args.not_reached or os.path.join(outdir, "not_reached.txt")
    if v:
        print(f"==> [4/4] analyzing from entries [{', '.join(args.entry)}]")
    result = analyze.analyze(
        merged, tc, args.entry, dot=args.dot,
        reached_out=reached, not_reached_out=not_reached, verbose=v,
    )
    with open(args.out, "w") as fh:
        json.dump(result, fh, indent=2)
    report.print_summary(result)
    print(f"wrote {args.out}")
    print(f"wrote {reached}  (sancov allowlist of reachable functions)")
    print(f"wrote {not_reached}  (sancov ignorelist of unreachable functions)")
    return 0


def cmd_check_toolchain(args):
    tc = toolchain.check_coherence(default_analyzer(), require_rust=True)
    print(f"OK: analyzer toolchain on LLVM {tc.llvm_major} "
          f"(min {toolchain.MIN_LLVM_MAJOR}); rustc LLVM {tc.rustc_major}")
    print(f"  clang     {tc.clang}")
    print(f"  clang++   {tc.clangxx}")
    print(f"  llvm-link {tc.llvm_link}")
    print(f"  analyzer  {tc.analyzer}")
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="reachability")
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
    r.add_argument("--dot", default=None)
    r.add_argument("--reached", default=None,
                   help="sancov allowlist path (default: reached.txt next to --out)")
    r.add_argument("--not-reached", default=None, dest="not_reached",
                   help="sancov ignorelist path (default: not_reached.txt next to --out)")
    r.add_argument("--out", default=None,
                   help="output JSON path, or a directory to write "
                        "reachability.json into (default: reachability.json "
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
            acquire_rust.AcquireError, link.LinkError, analyze.AnalyzeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
