; Minimal direct-call module: caller -> callee.
define i32 @callee() {
  ret i32 1
}

define i32 @caller() {
  %x = call i32 @callee()
  ret i32 %x
}
