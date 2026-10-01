@runner = internal global ptr @run

define internal i32 @handler(ptr %p) {
  ret i32 0
}

define internal i32 @other_arity(ptr %p, i64 %n) {
  ret i32 0
}

define internal void @run(ptr %o) {
  %cb = load ptr, ptr %o
  call void %cb(ptr %o)
  ret void
}

define i32 @LLVMFuzzerTestOneInput(ptr %d, i64 %n) {
  %obj = alloca ptr
  store ptr @handler, ptr %obj
  %spare = alloca ptr
  store ptr @other_arity, ptr %spare
  %r = load ptr, ptr @runner
  call void %r(ptr %obj)
  ret i32 0
}
