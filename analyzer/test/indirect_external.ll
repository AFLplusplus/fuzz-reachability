@external_slot = global ptr @external_callback

declare void @external_callback()

define void @entry() {
  %callee = load ptr, ptr @external_slot
  call void %callee()
  ret void
}
