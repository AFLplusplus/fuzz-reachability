declare void @register_cb(ptr)

@global_slot = global ptr @global_target

define internal void @target() {
  ret void
}

define internal void @global_target() {
  ret void
}

define internal void @struct_target() {
  ret void
}

define internal void @select_target() {
  ret void
}

define internal void @wrapper(ptr %cb) {
  call void @register_cb(ptr %cb)
  ret void
}

define i32 @entry() {
  %slot = alloca ptr
  store ptr @target, ptr %slot
  %cb = load ptr, ptr %slot
  call void @wrapper(ptr %cb)
  call void @register_cb(ptr @global_slot)
  %state = alloca { ptr }
  %field = getelementptr { ptr }, ptr %state, i32 0, i32 0
  store ptr @struct_target, ptr %field
  call void @register_cb(ptr %state)
  %selected = select i1 true, ptr @select_target, ptr @target
  call void @wrapper(ptr %selected)
  ret i32 0
}
