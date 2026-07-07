; Legacy-mangled Rust generic instances (…17h<16 hex>E). `entry` calls the
; `work` instance (reachable); the `dead` instance is unreachable. Proves the
; JSON `key` and the txt-list `*` glob both strip the codegen disambiguator.
define void @_ZN3app4work17h0123456789abcdefE() {
  ret void
}

define void @_ZN3app4dead17hfedcba9876543210E() {
  ret void
}

define void @entry() {
  call void @_ZN3app4work17h0123456789abcdefE()
  ret void
}
