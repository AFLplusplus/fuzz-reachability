// Generic monomorphization: `work` is instantiated at two concrete types,
// each producing its own mangled symbol (name + `17h<hash>` disambiguator).
#[inline(never)]
fn work<T: core::ops::Add<Output = T> + Copy>(x: T) -> T {
    x + x
}

#[no_mangle]
pub extern "C" fn LLVMFuzzerTestOneInput(data: *const u8, len: usize) -> i32 {
    if len == 0 {
        return 0;
    }
    let b = unsafe { *data };
    let a = work::<u32>(b as u32);
    let c = work::<u64>(b as u64);
    (a as i32).wrapping_add(c as i32)
}
