define i32 @main() {
  ret i32 0
}

define void @_ZN4demo4main17h1111111111111111E() {
  call void @rust_main_leaf()
  ret void
}

define void @rust_main_leaf() {
  ret void
}

define i32 @LLVMFuzzerTestOneInput(ptr %data, i64 %size) {
  call void @lf_leaf()
  ret i32 0
}

define void @lf_leaf() {
  ret void
}

define i32 @rust_fuzzer_test_input(ptr %data, i64 %size) {
  call void @rf_leaf()
  ret i32 0
}

define void @rf_leaf() {
  ret void
}

define void @orphan() {
  ret void
}
