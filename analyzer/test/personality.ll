define i32 @defined_personality(...) {
  ret i32 0
}

define void @entry() personality ptr @defined_personality {
  ret void
}
