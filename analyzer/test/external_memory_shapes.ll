target triple = "x86_64-unknown-linux-gnu"

declare ptr @ext_get_hooks()
declare ptr @ext_get_hooks2()
declare ptr @ext_get_ctx()
declare void @ext_get_hooks_out(ptr)
declare void @ext_register_init(ptr)
declare i64 @strtol(ptr, ptr, i32)
declare ptr @malloc(i64)
declare ptr @lib_init()

@g_ctx = internal global ptr null
@g_lib = internal global ptr null

define internal void @phi_cb(i32 %code) {
  ret void
}

define internal void @select_cb(i32 %code) {
  ret void
}

define internal void @accessor_cb(i32 %code) {
  ret void
}

define internal void @helper_cb(i32 %code) {
  ret void
}

define internal void @helper_arg_cb(i32 %code) {
  ret void
}

define internal void @outparam_cb(i32 %code) {
  ret void
}

define internal void @wrapped_outparam_cb(i32 %code) {
  ret void
}

define internal void @used_outparam_cb(i32 %code) {
  ret void
}

define internal void @local_slot_cb(i32 %code) {
  ret void
}

define internal void @libc_outparam_cb(i32 %code) {
  ret void
}

define internal void @reload_global_cb(i32 %code) {
  ret void
}

define internal void @reload_param_cb(i32 %code) {
  ret void
}

define internal void @stored_handle_cb(i32 %code) {
  ret void
}

define internal void @deref_cb(i32 %code) {
  ret void
}

define internal void @own_memory_cb(i32 %code) {
  ret void
}

define internal void @keep(ptr %p) {
  ret void
}

define internal void @setup(ptr %app) {
  %c1 = load ptr, ptr %app
  call void @ext_get_hooks_out(ptr %c1)
  %c2 = load ptr, ptr %app
  %h = load ptr, ptr %c2
  store ptr @reload_param_cb, ptr %h
  ret void
}

define internal ptr @deref(ptr %p) {
  %v = load ptr, ptr %p
  ret ptr %v
}

define internal ptr @maybe_hooks(i1 %c) {
entry:
  br i1 %c, label %get, label %done
get:
  %h = call ptr @ext_get_hooks()
  br label %done
done:
  %r = phi ptr [ %h, %get ], [ null, %entry ]
  ret ptr %r
}

define internal ptr @hooks_of(ptr %ctx) {
  %slot = getelementptr inbounds ptr, ptr %ctx, i64 1
  %h = load ptr, ptr %slot
  ret ptr %h
}

define internal void @install(ptr %h) {
  store ptr @helper_cb, ptr %h
  ret void
}

define internal void @my_init(ptr %h) {
  call void @install(ptr %h)
  ret void
}

define internal void @install_arg(ptr %h) {
  store ptr @helper_arg_cb, ptr %h
  ret void
}

define internal void @get_out(ptr %out) {
  call void @ext_get_hooks_out(ptr %out)
  ret void
}

define internal void @use_out(ptr %pp) {
  %h = load ptr, ptr %pp
  store ptr @used_outparam_cb, ptr %h
  ret void
}

define i32 @LLVMFuzzerTestOneInput(ptr %d, i64 %n) {
  %c = icmp ugt i64 %n, 1
  %m = call ptr @maybe_hooks(i1 %c)
  store ptr @phi_cb, ptr %m
  %a = call ptr @ext_get_hooks()
  %b = call ptr @ext_get_hooks2()
  %s = select i1 %c, ptr %a, ptr %b
  store ptr @select_cb, ptr %s
  %ctx = call ptr @ext_get_ctx()
  %acc = call ptr @hooks_of(ptr %ctx)
  store ptr @accessor_cb, ptr %acc
  call void @ext_register_init(ptr @my_init)
  %e = call ptr @ext_get_hooks()
  call void @install_arg(ptr %e)
  %o = alloca ptr
  call void @ext_get_hooks_out(ptr %o)
  %ov = load ptr, ptr %o
  store ptr @outparam_cb, ptr %ov
  %w = alloca ptr
  call void @get_out(ptr %w)
  %wv = load ptr, ptr %w
  store ptr @wrapped_outparam_cb, ptr %wv
  call void @use_out(ptr %o)
  %l = alloca ptr
  %lt = alloca ptr
  store ptr %lt, ptr %l
  call void @keep(ptr %l)
  %lv = load ptr, ptr %l
  store ptr @local_slot_cb, ptr %lv
  %end = alloca ptr
  %num = call i64 @strtol(ptr %d, ptr %end, i32 10)
  %ev = load ptr, ptr %end
  store ptr @libc_outparam_cb, ptr %ev
  %gm = call ptr @malloc(i64 16)
  store ptr %gm, ptr @g_ctx
  %g1 = load ptr, ptr @g_ctx
  call void @ext_get_hooks_out(ptr %g1)
  %g2 = load ptr, ptr @g_ctx
  %gh = load ptr, ptr %g2
  store ptr @reload_global_cb, ptr %gh
  %app = call ptr @malloc(i64 16)
  %actx = call ptr @malloc(i64 16)
  store ptr %actx, ptr %app
  call void @setup(ptr %app)
  %lib = call ptr @lib_init()
  store ptr %lib, ptr @g_lib
  %lib2 = load ptr, ptr @g_lib
  %lslot = getelementptr inbounds i8, ptr %lib2, i64 8
  store ptr @stored_handle_cb, ptr %lslot
  %dh = call ptr @deref(ptr %o)
  store ptr @deref_cb, ptr %dh
  %own = call ptr @malloc(i64 16)
  %ownp = alloca ptr
  store ptr %own, ptr %ownp
  %oh = call ptr @deref(ptr %ownp)
  store ptr @own_memory_cb, ptr %oh
  ret i32 0
}
