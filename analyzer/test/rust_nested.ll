define void @LLVMFuzzerTestOneInput() {
  call void @_ZN3foo3bar4quux17h0123456789abcdefE()
  ret void
}

define void @_ZN3foo3bar4quux17h0123456789abcdefE() {
  ret void
}

define void @_ZN3foo3bar17hfedcba9876543210E() {
  ret void
}
