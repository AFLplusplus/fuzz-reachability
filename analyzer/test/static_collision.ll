define internal i32 @helper(i32 %x) {
  %r = mul i32 %x, 2
  ret i32 %r
}

define i32 @b_unused(i32 %x) {
  %r = call i32 @helper(i32 %x)
  ret i32 %r
}

define internal i32 @helper.1(i32 %x) {
  %r = add i32 %x, 1
  ret i32 %r
}

define i32 @a_entry(i32 %x) {
  %r = call i32 @helper.1(i32 %x)
  ret i32 %r
}

define i32 @LLVMFuzzerTestOneInput(ptr %d, i64 %n) {
  %x = trunc i64 %n to i32
  %r = call i32 @a_entry(i32 %x)
  ret i32 %r
}
