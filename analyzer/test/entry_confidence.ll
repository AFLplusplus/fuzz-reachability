@slot = global ptr @real_target

declare void @register(ptr)

define void @real_target() {
  ret void
}

define void @decoy() {
  ret void
}

define void @entry() {
  %callee = load ptr, ptr @slot
  call void %callee()
  ret void
}

define void @unreachable_escape() {
  call void @register(ptr @decoy)
  ret void
}

define void @reached_escape() {
  call void @register(ptr @decoy)
  ret void
}
