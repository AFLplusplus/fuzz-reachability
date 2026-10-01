target triple = "x86_64-unknown-linux-gnu"

@slot_x = internal global ptr null
@hook_template = internal constant { ptr } { ptr @copied_hook }
@slot_c = internal global ptr null

declare ptr @ext_get_hooks()
declare ptr @ext_get_table()
declare void @llvm.memcpy.p0.p0.i64(ptr, ptr, i64, i1)
declare ptr @ext_get_ctx()
declare void @ext_register_init(ptr)
declare void @ext_register(ptr)
declare ptr @malloc(i64)
declare noalias ptr @pool_alloc(i64)

define internal void @copied_hook(i32 %code) {
  ret void
}

define internal void @my_error(i32 %code) {
  ret void
}

define internal void @wrapped_error(i32 %code) {
  ret void
}

define internal void @deep_error(i32 %code) {
  ret void
}

define internal void @heap_only(i32 %code) {
  ret void
}

define internal void @pool_only(i32 %code) {
  ret void
}

define internal void @my_cb(ptr %s) {
  ret void
}

define internal void @not_cb(ptr %s) {
  ret void
}

define internal void @orphan_cb(ptr %s) {
  ret void
}

define internal i32 @xchg_cb(ptr %p) {
  ret i32 0
}

define internal i32 @cas_cb(ptr %p) {
  ret i32 1
}

define internal void @never_published(ptr %p, i64 %n) {
  ret void
}

define internal ptr @get_hooks() {
  %h = call ptr @ext_get_hooks()
  ret ptr %h
}

define internal void @my_init(ptr %o) {
  store ptr @my_cb, ptr %o
  ret void
}

define internal void @not_external(ptr %o) {
  store ptr @not_cb, ptr %o
  ret void
}

define internal void @unused_registration() {
  call void @ext_register(ptr @orphan_cb)
  ret void
}

define i32 @LLVMFuzzerTestOneInput(ptr %d, i64 %n) {
  %h = call ptr @ext_get_hooks()
  store ptr @my_error, ptr %h
  %table = call ptr @ext_get_table()
  call void @llvm.memcpy.p0.p0.i64(ptr %table, ptr @hook_template, i64 8, i1 false)
  %w = call ptr @get_hooks()
  %wf = getelementptr inbounds ptr, ptr %w, i64 1
  store ptr @wrapped_error, ptr %wf
  %ctx = call ptr @ext_get_ctx()
  %inner.addr = getelementptr inbounds ptr, ptr %ctx, i64 2
  %inner = load ptr, ptr %inner.addr
  store ptr @deep_error, ptr %inner
  %heap = call ptr @malloc(i64 8)
  store ptr @heap_only, ptr %heap
  %pool = call ptr @pool_alloc(i64 8)
  store ptr @pool_only, ptr %pool
  call void @ext_register_init(ptr @my_init)
  %local = alloca ptr
  call void @not_external(ptr %local)
  %old = atomicrmw xchg ptr @slot_x, ptr @xchg_cb seq_cst
  %pair = cmpxchg ptr @slot_c, ptr null, ptr @cas_cb seq_cst seq_cst
  %spare = alloca ptr
  store ptr @never_published, ptr %spare
  %fx = load atomic ptr, ptr @slot_x seq_cst, align 8
  call void %fx(ptr %d, i64 %n)
  %fc = load atomic ptr, ptr @slot_c seq_cst, align 8
  call void %fc(ptr %d, i64 %n)
  ret i32 0
}
