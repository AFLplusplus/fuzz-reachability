@single = alias void (), ptr @real
@chain_one = alias void (), ptr @real
@chain_two = alias void (), ptr @chain_one
@invalid_target = global ptr null
@invalid_alias = alias void (), ptr @invalid_target
@cast_alias = alias void (), ptr addrspacecast (ptr addrspace(1) @address_space_real to ptr)
@declaration_alias = alias void (), ptr @external

declare void @external()

define void @real() {
  ret void
}

define void @address_space_real() addrspace(1) {
  ret void
}

define void @entry() {
  call void @single()
  call void @chain_two()
  call void @cast_alias()
  call void @declaration_alias()
  call void @invalid_alias()
  ret void
}
