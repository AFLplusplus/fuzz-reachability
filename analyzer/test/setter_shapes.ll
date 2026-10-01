%struct.obj = type { ptr, ptr }
%struct.tmpl = type { ptr, ptr }

@g_default = internal global ptr @via_global
@g_template = internal constant [2 x ptr] [ptr @via_template, ptr null]

declare void @llvm.memcpy.p0.p0.i64(ptr, ptr, i64, i1)

define internal i32 @via_offset(i32 %x) {
  ret i32 %x
}

define internal i32 @via_plain(i32 %x) {
  ret i32 %x
}

define internal i32 @via_global(i32 %x) {
  ret i32 %x
}

define internal i32 @via_init(i32 %x) {
  ret i32 %x
}

define internal i32 @sibling_field(i32 %x) {
  ret i32 %x
}

define internal i32 @via_ctor_arg(i32 %x) {
  ret i32 %x
}

define internal i32 @via_template(i32 %x) {
  ret i32 %x
}

define internal i32 @via_copy(i32 %x) {
  ret i32 %x
}

define internal i32 @via_select(i32 %x) {
  ret i32 %x
}

define internal i32 @via_select_b(i32 %x) {
  ret i32 %x
}

define internal i32 @via_phi(i32 %x) {
  ret i32 %x
}

define internal i32 @via_phi_b(i32 %x) {
  ret i32 %x
}

define internal i32 @via_memcpy(i32 %x) {
  ret i32 %x
}

define internal i32 @never_installed(i32 %x) {
  ret i32 %x
}

define internal i32 @copied_never_called(i32 %x) {
  ret i32 %x
}

define internal void @copy_cb(ptr %o, ptr %t) {
  %src = getelementptr inbounds %struct.tmpl, ptr %t, i32 0, i32 0
  %f = load ptr, ptr %src
  %dst = getelementptr inbounds %struct.obj, ptr %o, i32 0, i32 0
  store ptr %f, ptr %dst
  %bsrc = getelementptr inbounds %struct.tmpl, ptr %t, i32 0, i32 1
  %b = load ptr, ptr %bsrc
  %bdst = getelementptr inbounds %struct.obj, ptr %o, i32 0, i32 1
  store ptr %b, ptr %bdst
  ret void
}

define internal void @pick_cb(ptr %o, i1 %c) {
  %f = select i1 %c, ptr @via_select, ptr @via_select_b
  %dst = getelementptr inbounds %struct.obj, ptr %o, i32 0, i32 0
  store ptr %f, ptr %dst
  ret void
}

define internal void @phi_cb(ptr %o, i1 %c) {
entry:
  br i1 %c, label %left, label %right
left:
  br label %join
right:
  br label %join
join:
  %f = phi ptr [ @via_phi, %left ], [ @via_phi_b, %right ]
  %dst = getelementptr inbounds %struct.obj, ptr %o, i32 0, i32 0
  store ptr %f, ptr %dst
  ret void
}

define internal void @copy_struct(ptr %o, ptr %t) {
  call void @llvm.memcpy.p0.p0.i64(ptr %o, ptr %t, i64 16, i1 false)
  ret void
}

define internal void @copy_offset(ptr %o, ptr %t) {
  %src = getelementptr inbounds i8, ptr %t, i64 8
  %f = load ptr, ptr %src
  %dst = getelementptr inbounds i8, ptr %o, i64 8
  store ptr %f, ptr %dst
  ret void
}

define internal void @copy_plain(ptr %o, ptr %t) {
  %f = load ptr, ptr %t
  store ptr %f, ptr %o
  ret void
}

define internal void @set_default(ptr %o) {
  %f = load ptr, ptr @g_default
  store ptr %f, ptr %o
  ret void
}

define internal void @init_tmpl(ptr %t) {
  store ptr @via_init, ptr %t
  ret void
}

define internal void @copy_filled(ptr %o, ptr %t) {
  %f = load ptr, ptr %t
  store ptr %f, ptr %o
  ret void
}

define internal void @copy_data(ptr %o, ptr %t) {
  %src = getelementptr inbounds %struct.tmpl, ptr %t, i32 0, i32 1
  %v = load ptr, ptr %src
  %dst = getelementptr inbounds %struct.obj, ptr %o, i32 0, i32 1
  store ptr %v, ptr %dst
  ret void
}

define internal void @init_with(ptr %t, ptr %cb) {
  store ptr %cb, ptr %t
  ret void
}

define internal void @copy_ctor(ptr %o, ptr %t) {
  %f = load ptr, ptr %t
  store ptr %f, ptr %o
  ret void
}

define internal void @copy_template(ptr %o, ptr %t) {
  %f = load ptr, ptr %t
  store ptr %f, ptr %o
  ret void
}

define internal void @invoke(ptr %o, ptr %d) {
  %slot = getelementptr inbounds %struct.obj, ptr %o, i32 0, i32 0
  %f = load ptr, ptr %slot
  call void %f(ptr %d)
  ret void
}

define i32 @LLVMFuzzerTestOneInput(ptr %d, i64 %n) {
  %c = icmp ugt i64 %n, 1
  %buf = alloca [4 x i8]
  %t = alloca %struct.tmpl
  %tf = getelementptr inbounds %struct.tmpl, ptr %t, i32 0, i32 0
  store ptr @via_copy, ptr %tf
  %tb = getelementptr inbounds %struct.tmpl, ptr %t, i32 0, i32 1
  store ptr %buf, ptr %tb
  %a = alloca %struct.obj
  call void @copy_cb(ptr %a, ptr %t)
  call void @invoke(ptr %a, ptr %d)
  %b = alloca %struct.obj
  call void @pick_cb(ptr %b, i1 %c)
  call void @invoke(ptr %b, ptr %d)
  %p = alloca %struct.obj
  call void @phi_cb(ptr %p, i1 %c)
  call void @invoke(ptr %p, ptr %d)
  %t2 = alloca %struct.tmpl
  %t2f = getelementptr inbounds %struct.tmpl, ptr %t2, i32 0, i32 0
  store ptr @via_memcpy, ptr %t2f
  %e = alloca %struct.obj
  call void @copy_struct(ptr %e, ptr %t2)
  call void @invoke(ptr %e, ptr %d)
  %spare = alloca ptr
  store ptr @never_installed, ptr %spare
  %t3 = alloca %struct.tmpl
  %t3f = getelementptr inbounds %struct.tmpl, ptr %t3, i32 0, i32 0
  store ptr @copied_never_called, ptr %t3f
  %copy = alloca %struct.obj
  call void @llvm.memcpy.p0.p0.i64(ptr %copy, ptr %t3, i64 16, i1 false)
  %t4 = alloca [16 x i8]
  %t4f = getelementptr inbounds i8, ptr %t4, i64 8
  store ptr @via_offset, ptr %t4f
  %g = alloca [16 x i8]
  call void @copy_offset(ptr %g, ptr %t4)
  call void @invoke(ptr %g, ptr %d)
  %t5 = alloca [16 x i8]
  store ptr @via_plain, ptr %t5
  %h = alloca [16 x i8]
  call void @copy_plain(ptr %h, ptr %t5)
  call void @invoke(ptr %h, ptr %d)
  %k = alloca [16 x i8]
  call void @set_default(ptr %k)
  call void @invoke(ptr %k, ptr %d)
  %t6 = alloca [16 x i8]
  call void @init_tmpl(ptr %t6)
  %q = alloca [16 x i8]
  call void @copy_filled(ptr %q, ptr %t6)
  call void @invoke(ptr %q, ptr %d)
  %t7 = alloca %struct.tmpl
  %t7f = getelementptr inbounds %struct.tmpl, ptr %t7, i32 0, i32 0
  store ptr @sibling_field, ptr %t7f
  %t7b = getelementptr inbounds %struct.tmpl, ptr %t7, i32 0, i32 1
  store ptr %buf, ptr %t7b
  %r = alloca %struct.obj
  call void @copy_data(ptr %r, ptr %t7)
  call void @invoke(ptr %r, ptr %d)
  %t8 = alloca [16 x i8]
  call void @init_with(ptr %t8, ptr @via_ctor_arg)
  %s = alloca [16 x i8]
  call void @copy_ctor(ptr %s, ptr %t8)
  call void @invoke(ptr %s, ptr %d)
  %t9 = alloca [16 x i8]
  call void @llvm.memcpy.p0.p0.i64(ptr %t9, ptr @g_template, i64 16, i1 false)
  %u = alloca [16 x i8]
  call void @copy_template(ptr %u, ptr %t9)
  call void @invoke(ptr %u, ptr %d)
  ret i32 0
}
