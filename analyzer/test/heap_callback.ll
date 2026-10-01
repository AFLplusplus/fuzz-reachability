%struct.table = type { ptr }

declare ptr @malloc(i64)
declare void @free(ptr)

define internal i32 @free_entry(ptr %p) {
  %p.addr = alloca ptr
  store ptr %p, ptr %p.addr
  %v = load ptr, ptr %p.addr
  call void @free(ptr %v)
  ret i32 0
}

define ptr @table_new(ptr %fn) {
  %fn.addr = alloca ptr
  %t = alloca ptr
  store ptr %fn, ptr %fn.addr
  %call = call ptr @malloc(i64 8)
  store ptr %call, ptr %t
  %f = load ptr, ptr %fn.addr
  %t1 = load ptr, ptr %t
  %slot = getelementptr inbounds %struct.table, ptr %t1, i32 0, i32 0
  store ptr %f, ptr %slot
  %t2 = load ptr, ptr %t
  ret ptr %t2
}

define void @table_free(ptr %t, ptr %v) {
  %t.addr = alloca ptr
  %v.addr = alloca ptr
  store ptr %t, ptr %t.addr
  store ptr %v, ptr %v.addr
  %t1 = load ptr, ptr %t.addr
  %slot = getelementptr inbounds %struct.table, ptr %t1, i32 0, i32 0
  %fn = load ptr, ptr %slot
  %v1 = load ptr, ptr %v.addr
  call void %fn(ptr %v1)
  ret void
}

define i32 @LLVMFuzzerTestOneInput(ptr %d, i64 %n) {
  %t = alloca ptr
  %call = call ptr @table_new(ptr @free_entry)
  store ptr %call, ptr %t
  %t1 = load ptr, ptr %t
  %buf = call ptr @malloc(i64 %n)
  call void @table_free(ptr %t1, ptr %buf)
  ret i32 0
}
