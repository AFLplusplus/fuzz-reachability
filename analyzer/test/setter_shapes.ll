%struct.obj = type { ptr, ptr }
%struct.tmpl = type { ptr, ptr }

declare void @llvm.memcpy.p0.p0.i64(ptr, ptr, i64, i1)

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
  ret i32 0
}
