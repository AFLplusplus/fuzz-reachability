@llvm.global_ctors = appending global [2 x { i32, ptr, ptr }] [{ i32, ptr, ptr } { i32 100, ptr @constructor, ptr null }, { i32, ptr, ptr } { i32 200, ptr null, ptr null }]
@llvm.global_dtors = appending global [1 x { i32, ptr, ptr }] [{ i32, ptr, ptr } { i32 100, ptr @destructor, ptr null }]
@resolved = ifunc void (), ptr @ifunc_resolver

define void @constructor_leaf() {
  ret void
}

define void @constructor() {
  call void @constructor_leaf()
  ret void
}

define void @destructor() {
  ret void
}

define ptr @ifunc_resolver() {
  ret ptr @ifunc_implementation
}

define void @ifunc_implementation() {
  ret void
}

define i32 @LLVMFuzzerInitialize(ptr %argc, ptr %argv) {
  ret i32 0
}

define void @entry() {
  ret void
}
