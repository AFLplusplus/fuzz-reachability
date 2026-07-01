from reachability.diagnostics import diagnose_build


def test_lto_skip_line_detected():
    log = ("WARNING: We are skipping bitcode generation because we are doing "
           "link time optimization, and so the compiler is doing the job for us.")
    cause, remedy = diagnose_build(log, [])
    assert "link-time optimization" in cause
    assert "CFLAGS_FLTO" in remedy
    assert "CMAKE_INTERPROCEDURAL_OPTIMIZATION" in remedy


def test_lto_via_getbc_error_and_flto_in_log():
    log = "gclang -O2 -flto=full -c foo.c"
    cause, _ = diagnose_build(
        log, ["afl-fuzz: ERROR:Error reading the .llvm_bc section of ELF file"])
    assert "link-time optimization" in cause


def test_getbc_error_without_lto_is_afl_cc():
    cause, remedy = diagnose_build(
        "clang -O2 -c foo.c",
        ["ERROR:Error reading the .llvm_bc section of ELF file bin"])
    assert ".llvm_bc" in cause
    assert "gclang" in remedy


def test_ccache_detected():
    cause, remedy = diagnose_build("ccache clang -c foo.c", [])
    assert "cache" in cause.lower()
    assert "ccache -C" in remedy


def test_assembly_only_detected():
    log = ("We are skipping bitcode generation because the input file(s) are "
           "written in assembly.")
    cause, _ = diagnose_build(log, [])
    assert "no bitcode" in cause


def test_clean_log_returns_none():
    assert diagnose_build("gclang -c foo.c -o foo.o\n", []) is None


def test_lto_precedes_ccache_and_afl():
    log = ("ccache gclang -flto=full -c foo.c\nWARNING: We are skipping bitcode "
           "generation because we are doing link time optimization.")
    cause, _ = diagnose_build(log, ["Error reading the .llvm_bc section"])
    assert "link-time optimization" in cause
