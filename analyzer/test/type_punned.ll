@punned_slot = global ptr @punned_target
@decoy_slot = global ptr @exact_decoy

define i32 @punned_target(i32 %value) {
  ret i32 %value
}

define void @exact_decoy() {
  ret void
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
