; Indirect call of type i32(i32). opt_a and opt_b are address-taken and match;
; other has a different type; take is unreachable from entry.
@g = global ptr @opt_a
@other_slot = global ptr @other

define i32 @opt_a(i32 %x) {
  ret i32 %x
}

define i32 @opt_b(i32 %x) {
  ret i32 %x
}

define i32 @other(i32 %x, i32 %y) {
  ret i32 %x
}

define i32 @entry(ptr %fp, i32 %v) {
  %r = call i32 %fp(i32 %v)
  ret i32 %r
}

define void @take() {
  store ptr @opt_b, ptr @g
  ret void
}
