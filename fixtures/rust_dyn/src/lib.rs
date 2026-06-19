trait Op {
    fn run(&self, x: u8) -> u8;
}

struct Inc;
impl Op for Inc {
    fn run(&self, x: u8) -> u8 {
        x.wrapping_add(1)
    }
}

struct Dbl;
impl Op for Dbl {
    fn run(&self, x: u8) -> u8 {
        x.wrapping_mul(2)
    }
}

#[no_mangle]
pub extern "C" fn LLVMFuzzerTestOneInput(data: *const u8, len: usize) -> i32 {
    if len == 0 {
        return 0;
    }
    let b = unsafe { *data };
    // Trait-object dispatch: an indirect call resolved via the vtable.
    let op: Box<dyn Op> = if b & 1 == 1 { Box::new(Inc) } else { Box::new(Dbl) };
    op.run(b) as i32
}
