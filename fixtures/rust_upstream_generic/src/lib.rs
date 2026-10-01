#[no_mangle]
pub extern "C" fn LLVMFuzzerTestOneInput(data: *const u8, len: usize) -> i32 {
    if len < 2 {
        return 0;
    }
    let input = unsafe { core::slice::from_raw_parts(data, len) };
    let head = dep::checksum::<u8>(&input[..len / 2]);
    let tail = dep::checksum::<u8>(&input[len / 2..]);
    let both = dep::dep_checksum(input);
    (head ^ tail ^ both) as i32
}
