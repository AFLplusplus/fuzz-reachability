// A ziggy/afl-style harness shape: the entry is the Rust `main`, reached via the
// flexible `--entry main` resolution (no mangled symbol needed).

#[inline(never)]
fn reached_one() -> u8 {
    1
}

#[inline(never)]
fn reached_two() -> u8 {
    reached_one().wrapping_add(1)
}

#[no_mangle]
pub extern "C" fn not_reached() -> u8 {
    99
}

fn main() {
    std::process::exit(reached_two() as i32);
}
