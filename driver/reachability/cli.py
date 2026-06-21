"""Command-line front-end: chains the pipeline stages end-to-end.

  reachability check-toolchain
  reachability run --project DIR --lang {c,cpp,rust,mixed,libfuzzer,ziggy,afl} --out FILE [...]
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


def default_analyzer():
    env = os.environ.get("REACHABILITY_ANALYZER")
    if env:
        return env
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    return os.path.join(repo, "analyzer", "build", "reachability-analyzer")


def _acquire(args, tc):
    """Return the list of .bc files for the project per --lang."""
    mode = TARGETS[args.lang][0]
    bcs = []
    if mode in ("c", "cpp", "mixed"):
        # An explicit --build-cmd wins; otherwise auto-detect the build system
        # from the project's files. Either runs under a shell so it can be a
        # compound command; gllvm wrappers are injected via env.
        build = args.build_cmd or acquire_c.detect_build_cmd(args.project)
        if not args.build_cmd:
            print(f"build command: {build or 'make'}"
                  f"{'' if build else ' (default; no build system detected)'}")
        build_cmd = ["sh", "-c", build] if build else None
        bcs.extend(
            acquire_c.acquire_c_bitcode(
                args.project, tc, args.artifact, build_cmd,
                static_libs=args.static_libs,
            )
        )
    if mode in ("rust", "mixed"):
        bcs.extend(
            acquire_rust.acquire_rust_bitcode(args.project, build_std=args.build_std)
        )
    return bcs


def cmd_run(args):
    tc = toolchain.check_coherence(default_analyzer())
    if TARGETS[args.lang][0] in ("rust", "mixed"):
        toolchain.assert_rust_bitcode_readable(tc)
    bcs = _acquire(args, tc)
    merged = os.path.join(args.project, "merged.bc")
    link.link_bitcode(bcs, merged, tc)
    # The two sancov lists land next to the JSON output (override with flags).
    outdir = os.path.dirname(os.path.abspath(args.out))
    reached = args.reached or os.path.join(outdir, "reached.txt")
    not_reached = args.not_reached or os.path.join(outdir, "not_reached.txt")
    result = analyze.analyze(
        merged, tc, args.entry, backend=args.backend, dot=args.dot,
        reached_out=reached, not_reached_out=not_reached,
    )
    with open(args.out, "w") as fh:
        json.dump(result, fh, indent=2)
    report.print_summary(result, verbose=args.verbose)
    print(f"wrote {args.out}")
    print(f"wrote {reached}  (sancov allowlist of reachable functions)")
    print(f"wrote {not_reached}  (sancov ignorelist of unreachable functions)")
    return 0


def cmd_check_toolchain(args):
    tc = toolchain.check_coherence(default_analyzer())
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
                   help="shell build command for C/C++ (default: auto-detected "
                        "from configure/Makefile/CMakeLists.txt/build.ninja/"
                        "meson.build, else make); "
                        "e.g. 'cmake -S . -B build && cmake --build build'")
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
    r.add_argument("--backend", default="type-based", choices=["type-based", "svf"])
    r.add_argument("--build-std", action="store_true", dest="build_std")
    r.add_argument("--dot", default=None)
    r.add_argument("--reached", default=None,
                   help="sancov allowlist path (default: reached.txt next to --out)")
    r.add_argument("--not-reached", default=None, dest="not_reached",
                   help="sancov ignorelist path (default: not_reached.txt next to --out)")
    r.add_argument("--out", required=True)
    r.add_argument("-v", "--verbose", action="store_true")
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
