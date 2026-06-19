from reachability import acquire_rust


def test_rustflags_contains_emit_bc():
    flags = acquire_rust._rustflags(build_std=False)
    assert "--emit=llvm-bc" in flags
    assert "-Cembed-bitcode=yes" in flags
    assert "-Ccodegen-units=1" in flags


def test_rustflags_build_std():
    assert "-Zbuild-std" in acquire_rust._rustflags(build_std=True)
