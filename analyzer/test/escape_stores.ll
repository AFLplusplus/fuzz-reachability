@null_slot = global ptr null
@nonnull_slot = global ptr @initial_callback
@aggregate = global { ptr, [2 x ptr] } zeroinitializer

declare void @register(ptr)
declare ptr @malloc(i64)

define void @initial_callback() {
  ret void
}

define void @null_callback() {
  ret void
}

define void @first_callback() {
  ret void
}

define void @second_callback() {
  ret void
}

define void @struct_callback() {
  ret void
}

define void @array_callback() {
  ret void
}

define void @heap_callback() {
  ret void
}

define void @stack_callback() {
  ret void
}

define void @entry() {
  store ptr @null_callback, ptr @null_slot
  call void @register(ptr @null_slot)
  store ptr @first_callback, ptr @nonnull_slot
  store ptr @second_callback, ptr @nonnull_slot
  call void @register(ptr @nonnull_slot)
  %field = getelementptr { ptr, [2 x ptr] }, ptr @aggregate, i32 0, i32 0
  store ptr @struct_callback, ptr %field
  %element = getelementptr { ptr, [2 x ptr] }, ptr @aggregate, i32 0, i32 1, i32 1
  store ptr @array_callback, ptr %element
  call void @register(ptr @aggregate)
  %heap = call ptr @malloc(i64 16)
  store ptr @heap_callback, ptr %heap
  call void @register(ptr %heap)
  %stack = alloca ptr
  store ptr @stack_callback, ptr %stack
  call void @register(ptr %stack)
  ret void
}
