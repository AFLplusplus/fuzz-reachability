; `entry` calls a defined `local`, an undefined `ext` (a precompiled-lib
; stand-in), and the LLVM intrinsic `llvm.donothing`. `ext` is reachable but
; has no body, so it is an external declaration; `llvm.donothing` is also a
; declaration but is lowered by the backend, not real external code, so it
; must NOT be counted as an external declaration.
declare void @ext()
declare void @llvm.donothing()

define void @local() {
  ret void
}

define void @entry() {
  call void @local()
  call void @ext()
  call void @llvm.donothing()
  ret void
}
