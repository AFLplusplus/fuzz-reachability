%struct.Obj = type { ptr, ptr, ptr, ptr, ptr, i32 }

@_ZTV4Impl = constant { [4 x ptr] } { [4 x ptr] [ptr null, ptr null, ptr @Impl_run, ptr @Impl_other] }

define i32 @field_called(ptr %o) {
  ret i32 1
}

define i32 @field_default(ptr %o) {
  ret i32 2
}

define i32 @field_unused(ptr %o) {
  ret i32 3
}

define i32 @Impl_run(ptr %self) {
  ret i32 4
}

define i32 @Impl_other(ptr %self) {
  ret i32 5
}

define void @set_defaults(ptr %o) {
  %f1 = getelementptr inbounds %struct.Obj, ptr %o, i32 0, i32 1
  store ptr @field_unused, ptr %f1
  %f4 = getelementptr inbounds %struct.Obj, ptr %o, i32 0, i32 4
  store ptr @field_default, ptr %f4
  ret void
}

define void @override(ptr %o) {
  %f4 = getelementptr inbounds %struct.Obj, ptr %o, i32 0, i32 4
  store ptr @field_called, ptr %f4
  ret void
}

define void @Impl_ctor(ptr %this) {
  store ptr getelementptr inbounds inrange(-16, 16) ({ [4 x ptr] }, ptr @_ZTV4Impl, i32 0, i32 0, i32 2), ptr %this
  ret void
}

define i32 @entry(ptr %o, ptr %obj) {
  call void @set_defaults(ptr %o)
  call void @override(ptr %o)
  %f4 = getelementptr inbounds %struct.Obj, ptr %o, i32 0, i32 4
  %m = load ptr, ptr %f4
  %r1 = call i32 %m(ptr %o)
  call void @Impl_ctor(ptr %obj)
  %vptr = load ptr, ptr %obj
  %vfn = getelementptr inbounds ptr, ptr %vptr, i64 0
  %run = load ptr, ptr %vfn
  %r2 = call i32 %run(ptr %obj)
  %sum = add i32 %r1, %r2
  ret i32 %sum
}
