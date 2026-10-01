@_ZTV4Used = constant { [4 x ptr] } { [4 x ptr] [ptr null, ptr null, ptr @_ZN4Used5firstEPv, ptr @_ZN4Used3runEPv] }
@_ZTV5Never = constant { [3 x ptr] } { [3 x ptr] [ptr null, ptr null, ptr @_ZN5Never9unrelatedEPv] }
@vtable.0 = private constant <{ ptr, [16 x i8], ptr }> <{ ptr @_ZN4core3ptr13drop_in_place17h0123456789abcdefE, [16 x i8] c"\08\00\00\00\00\00\00\00\08\00\00\00\00\00\00\00", ptr @_ZN5thing6method17h2222222222222222E }>
@table = internal constant [1 x ptr] [ptr @target]

declare ptr @ext_vtable()

define void @_ZN4UsedC2Ev(ptr %this) {
  store ptr getelementptr inbounds inrange(-16, 16) ({ [4 x ptr] }, ptr @_ZTV4Used, i32 0, i32 0, i32 2), ptr %this
  ret void
}

define void @_ZN5NeverC2Ev(ptr %this) {
  store ptr getelementptr inbounds inrange(-16, 8) ({ [3 x ptr] }, ptr @_ZTV5Never, i32 0, i32 0, i32 2), ptr %this
  ret void
}

define void @_ZN4Used5firstEPv(ptr %this, ptr %p) {
  ret void
}

define void @_ZN4Used3runEPv(ptr %this, ptr %p) {
  ret void
}

define void @_ZN5Never9unrelatedEPv(ptr %this, ptr %p) {
  ret void
}

define void @target(ptr %a, ptr %b) {
  ret void
}

define void @_ZN4core3ptr13drop_in_place17h0123456789abcdefE(ptr %x) {
  ret void
}

define void @_ZN5thing6method17h2222222222222222E(ptr %x) {
  ret void
}

define void @call_first(ptr %pp, ptr %a, ptr %b) {
  %f = load ptr, ptr %pp
  call void %f(ptr %a, ptr %b)
  ret void
}

define void @c_field_call(ptr %h, ptr %a, ptr %b) {
  %fns = load ptr, ptr %h
  %f = load ptr, ptr %fns
  call void %f(ptr %a, ptr %b)
  ret void
}

define void @c_drop(ptr %pp, ptr %x) {
  %f = load ptr, ptr %pp
  call void %f(ptr %x)
  ret void
}

define void @_ZN5thing4call17h1111111111111111E(ptr %data) {
  %vtable = call ptr @ext_vtable()
  %slot = getelementptr inbounds i8, ptr %vtable, i64 24
  %m = load ptr, ptr %slot
  call void %m(ptr %data)
  ret void
}

define void @_Z8dispatchP4UsedPv(ptr %obj, ptr %p) {
  %vptr = load ptr, ptr %obj
  %slot = getelementptr inbounds ptr, ptr %vptr, i64 1
  %m = load ptr, ptr %slot
  call void %m(ptr %obj, ptr %p)
  ret void
}

define i32 @LLVMFuzzerTestOneInput(ptr %d, i64 %n) {
  %obj = alloca ptr
  %pp = alloca ptr
  %holder = alloca ptr
  call void @call_first(ptr @table, ptr %d, ptr null)
  call void @c_field_call(ptr %holder, ptr %d, ptr null)
  call void @c_drop(ptr %pp, ptr %d)
  call void @_ZN5thing4call17h1111111111111111E(ptr %d)
  call void @_Z8dispatchP4UsedPv(ptr %obj, ptr %d)
  ret i32 0
}
