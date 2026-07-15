@punned_slot = global ptr @punned_target
@decoy_slot = global ptr @exact_decoy
@hidden_slot = global ptr @hidden_target

define i32 @punned_target(i32 %value) {
  ret i32 %value
}

define i32 @punned_other(i32 %value) {
  ret i32 %value
}

define void @exact_decoy() {
  ret void
}

define i64 @hidden_target(i64 %value) {
  ret i64 %value
}

define void @entry() {
  %callee = load ptr, ptr @punned_slot
  call void %callee()
  ret void
}

define void @control(ptr %callee) {
  call void %callee()
  ret void
}

define void @partially_laundered(i1 %choose, i64 %rawbits) {
  %known = load ptr, ptr @punned_slot
  %raw = inttoptr i64 %rawbits to ptr
  %callee = select i1 %choose, ptr %known, ptr %raw
  call void %callee()
  ret void
}

define void @resolved_integer_roundtrip(i1 %choose) {
  %bits = select i1 %choose, i64 ptrtoint (ptr @punned_target to i64), i64 ptrtoint (ptr @punned_other to i64)
  %callee = inttoptr i64 %bits to ptr
  call void %callee()
  ret void
}

define void @stored_integer_laundering(i64 %rawbits) {
  %slot = alloca ptr
  %raw = inttoptr i64 %rawbits to ptr
  store ptr %raw, ptr %slot
  %callee = load ptr, ptr %slot
  call void %callee()
  ret void
}
