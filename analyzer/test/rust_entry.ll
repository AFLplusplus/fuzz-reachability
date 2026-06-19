; Rust-style fuzz entry with no LLVMFuzzerTestOneInput glue present.
define i32 @rust_fuzzer_test_input(ptr %d, i64 %n) {
  %r = call i32 @inner(i64 %n)
  ret i32 %r
}

define i32 @inner(i64 %n) {
  ret i32 0
}
