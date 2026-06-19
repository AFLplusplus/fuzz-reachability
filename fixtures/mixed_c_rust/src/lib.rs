fn parse(b: u8) -> u8 {
    b.wrapping_add(7)
}

fn never_called(b: u8) -> u8 {
    b
}

// The Rust fuzz entry (as libfuzzer-sys would generate). Reached from the C++
// glue only because both sides' bitcode is merged and the C ABI symbol matches.
#[no_mangle]
pub extern "C" fn rust_fuzzer_test_input(data: *const u8, len: usize) -> i32 {
    if len == 0 {
        return 0;
    }
    parse(unsafe { *data }) as i32
}
