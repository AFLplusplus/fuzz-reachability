#[cfg(debug_assertions)]
#[no_mangle]
pub extern "C" fn assertions_on() {}

#[cfg(not(debug_assertions))]
#[no_mangle]
pub extern "C" fn assertions_off() {}

#[no_mangle]
pub extern "C" fn profile_entry() {
    #[cfg(debug_assertions)]
    assertions_on();
    #[cfg(not(debug_assertions))]
    assertions_off();
}
