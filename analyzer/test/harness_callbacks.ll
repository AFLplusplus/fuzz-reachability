@llvm.global_ctors = appending global [1 x { i32, ptr, ptr }] [{ i32, ptr, ptr } { i32 65535, ptr @ctor, ptr null }]
@ext_error_hook = external global ptr
@ext_table = external global ptr
@keep = global ptr @never_handed
@static_obj = internal global ptr null
@internal_slot = internal global ptr null
@__dso_handle = external hidden global i8

declare void @ext_register(ptr)
declare void @ext_parse(ptr, i64)
declare i32 @atexit(ptr)
declare i32 @__cxa_atexit(ptr, ptr, ptr)

define internal void @on_init_record(ptr %d, i64 %n) {
  ret void
}

define internal void @on_ctor_record(ptr %d, i64 %n) {
  ret void
}

define internal void @my_hook(i32 %code) {
  ret void
}

define internal void @table_hook(i32 %code) {
  ret void
}

define internal void @never_handed(i64 %x) {
  ret void
}

define internal void @at_exit_only() {
  ret void
}

define internal void @static_dtor(ptr %obj) {
  ret void
}

define internal void @returned_only() {
  ret void
}

define internal ptr @pick_callback() {
  ret ptr @returned_only
}

define internal void @register_all() {
  call void @ext_register(ptr @on_ctor_record)
  ret void
}

define internal void @ctor() {
  call void @register_all()
  %a = call i32 @atexit(ptr @at_exit_only)
  %d = call i32 @__cxa_atexit(ptr @static_dtor, ptr @static_obj, ptr @__dso_handle)
  %cb = call ptr @pick_callback()
  store ptr %cb, ptr @internal_slot
  ret void
}

define i32 @LLVMFuzzerInitialize(ptr %argc, ptr %argv) {
  call void @ext_register(ptr @on_init_record)
  ret i32 0
}

define i32 @LLVMFuzzerTestOneInput(ptr %d, i64 %n) {
  store ptr @my_hook, ptr @ext_error_hook
  %table = load ptr, ptr @ext_table
  %slot = getelementptr inbounds ptr, ptr %table, i64 3
  store ptr @table_hook, ptr %slot
  call void @ext_parse(ptr %d, i64 %n)
  ret i32 0
}

define void @other_entry() {
  ret void
}
