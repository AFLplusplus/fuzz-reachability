declare void @external()

define void @callback() {
  ret void
}

define void @entry() {
  call void @external() [ "callback"(ptr @callback) ]
  ret void
}
