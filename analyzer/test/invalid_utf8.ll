define void @"bad\FF"() {
  ret void
}

define void @entry() {
  call void @"bad\FF"()
  ret void
}
